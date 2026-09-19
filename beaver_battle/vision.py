"""Latest-frame UVC capture, planar calibration, physical walls, and red aims.

Preview arrays are BGR camera pixels. Aims and walls use logical display pixels.
No camera control (exposure, gain, white balance) is changed by this module.
"""

from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from types import MappingProxyType

import cv2
import numpy as np

from beaver_battle.model import VisionSnapshot


def readonly(array):
    result = array.copy()
    result.setflags(write=False)
    return result


def marker_layout(width, height):
    size = max(24, min(width, height) // 7)
    margin = max(12, size // 3)
    origins = [(margin, margin), (width - margin - size, margin),
               (width - margin - size, height - margin - size),
               (margin, height - margin - size)]
    return {number: np.float32([(x, y), (x + size - 1, y),
                               (x + size - 1, y + size - 1), (x, y + size - 1)])
            for number, (x, y) in enumerate(origins)}


def calibration_image(width, height):
    canvas = np.full((height, width, 3), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for number, corners in marker_layout(width, height).items():
        x, y = corners[0].astype(int)
        size = int(corners[1, 0] - x + 1)
        marker = cv2.aruco.generateImageMarker(dictionary, number, size)
        canvas[y:y + size, x:x + size] = marker[:, :, None]
    return canvas


def find_calibration(frame, width, height):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, rejected = cv2.aruco.ArucoDetector(dictionary, parameters).detectMarkers(frame)
    if ids is None:
        return None
    found = {int(number): points.reshape(4, 2) for number, points in zip(ids.flatten(), corners)}
    if any(list(ids.flatten()).count(number) != 1 for number in range(4)):
        return None
    source = np.concatenate([found[number] for number in range(4)])
    destination = np.concatenate(list(marker_layout(width, height).values()))
    matrix, inliers = cv2.findHomography(source, destination, cv2.RANSAC, 3.0)
    if matrix is None or inliers is None or int(inliers.sum()) < 14:
        return None
    projected = cv2.perspectiveTransform(source[None], matrix)[0]
    if not np.isfinite(matrix).all() or np.max(np.linalg.norm(projected - destination, axis=1)) > 5:
        return None
    if abs(np.linalg.det(matrix)) < 1e-9:
        return None
    return matrix


def save_calibration(path, matrix, camera_shape, display_shape):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("wb") as handle:
        np.savez(handle, matrix=matrix, camera_shape=camera_shape, display_shape=display_shape)
    temporary.replace(target)


def load_calibration(path, camera_shape, display_shape):
    with np.load(path, allow_pickle=False) as saved:
        if tuple(saved["camera_shape"]) != tuple(camera_shape):
            raise ValueError("Calibration camera size changed; recalibrate")
        if tuple(saved["display_shape"]) != tuple(display_shape):
            raise ValueError("Calibration display size changed; recalibrate")
        matrix = saved["matrix"].copy()
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-9:
        raise ValueError("Invalid saved calibration; recalibrate")
    return matrix


def laser_candidates(frame, matrix, width, height, min_area=1, max_area=180):
    """Find red components before warping, preserving small physical laser spots."""
    blue, green, red = cv2.split(frame)
    redness = red.astype(np.int16) - np.maximum(blue, green).astype(np.int16)
    mask = ((red >= 160) & (redness >= 60)).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    points = [centers[index] for index in range(1, count)
              if min_area <= stats[index, cv2.CC_STAT_AREA] <= max_area]
    if not points:
        return []
    transformed = cv2.perspectiveTransform(np.float32(points)[None], matrix)[0]
    return [tuple(map(float, point)) for point in transformed
            if np.isfinite(point).all() and 0 <= point[0] < width and 0 <= point[1] < height]


@dataclass
class WallFilter:
    persistence: int = 3
    evidence: np.ndarray | None = None
    mask: np.ndarray | None = None

    def update(self, dark):
        limit = max(1, min(127, int(self.persistence)))
        if self.evidence is None or self.evidence.shape != dark.shape:
            self.evidence = np.zeros(dark.shape, np.int16)
            self.mask = np.zeros(dark.shape, bool)
        self.evidence = np.where(dark, np.maximum(self.evidence, 0) + 1,
                                 np.minimum(self.evidence, 0) - 1).clip(-limit, limit)
        self.mask[self.evidence >= limit] = True
        self.mask[self.evidence <= -limit] = False
        return readonly(self.mask)


@dataclass
class Track:
    position: np.ndarray
    timestamp: float
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    valid: bool = True


@dataclass
class LaserTracker:
    stale_seconds: float = 0.5
    tracks: dict = field(default_factory=dict)
    max_speed: float = 1200.0
    ambiguity_pixels: float = 18.0

    def accept(self, player_id, point, timestamp, identified=False):
        point = np.asarray(point, dtype=float)
        previous = self.tracks.get(player_id)
        velocity = np.zeros(2)
        if previous is not None and not identified:
            elapsed = max(0.001, timestamp - previous.timestamp)
            velocity = (point - previous.position) / elapsed
            speed = np.linalg.norm(velocity)
            if speed > self.max_speed:
                velocity *= self.max_speed / speed
            velocity = 0.5 * previous.velocity + 0.5 * velocity
        self.tracks[player_id] = Track(point, timestamp, velocity)

    def update(self, points, timestamp, identity=None, ready_since=0.0):
        aims = {}
        confidence = {player_id: 0.0 for player_id in self.tracks}
        if timestamp < ready_since:
            return aims, confidence
        if identity is not None:
            if identity in (1, 2, 3) and len(points) == 1:
                self.accept(identity, points[0], timestamp, identified=True)
                aims[identity] = tuple(points[0])
                confidence[identity] = 1.0
            elif identity in self.tracks:
                self.tracks[identity].valid = False
            return aims, confidence

        active = {}
        for player_id, track in self.tracks.items():
            if timestamp - track.timestamp > self.stale_seconds:
                track.valid = False
            if track.valid:
                active[player_id] = track
        if not active:
            return aims, confidence
        if len(points) > 12:
            for track in active.values():
                track.valid = False
            return aims, confidence

        locations = np.asarray(points, dtype=float).reshape(-1, 2)
        choices = {}
        costs = {}
        for player_id, track in active.items():
            elapsed = max(0.0, timestamp - track.timestamp)
            prediction = track.position + track.velocity * min(elapsed, 0.1)
            gate = min(180.0, 24.0 + self.max_speed * elapsed)
            distances = np.linalg.norm(locations - prediction, axis=1)
            choices[player_id] = [int(index) for index in np.flatnonzero(distances <= gate)]
            costs[player_id] = distances ** 2
        players = list(active)
        possibilities = []
        for assignment in product(*[choices[player_id] + [-1] for player_id in players]):
            used = [index for index in assignment if index >= 0]
            if len(set(used)) != len(used):
                continue
            cost = sum(costs[player_id][index] for player_id, index in zip(players, assignment) if index >= 0)
            possibilities.append((-len(used), float(cost), assignment))
        possibilities.sort()
        best = possibilities[0]
        uncertain = set()
        for alternative in possibilities[1:]:
            if alternative[0] != best[0] or alternative[1] - best[1] > self.ambiguity_pixels ** 2:
                break
            uncertain.update(player_id for player_id, a, b in zip(players, best[2], alternative[2]) if a != b)
        # A merged spot must invalidate all competing identities, even if one is closer.
        for player_id, index in zip(players, best[2]):
            if index == -1:
                uncertain.add(player_id)
                for other in players:
                    if set(choices[player_id]) & set(choices[other]):
                        uncertain.add(other)
        for player_id, index in zip(players, best[2]):
            if player_id in uncertain or index < 0:
                active[player_id].valid = False
                continue
            point = locations[index]
            # Very close blobs cannot carry reliable independent same-color identities.
            neighbors = np.linalg.norm(locations - point, axis=1)
            if np.any((neighbors > 0) & (neighbors < self.ambiguity_pixels)):
                active[player_id].valid = False
                continue
            self.accept(player_id, point, timestamp)
            aims[player_id] = tuple(map(float, point))
            confidence[player_id] = 0.9
        return aims, confidence


@dataclass
class Vision:
    config: dict
    lock: object = field(default_factory=Lock, init=False)
    stopping: Event = field(default_factory=Event, init=False)
    frame_ready: Event = field(default_factory=Event, init=False)
    calibration_requested: Event = field(default_factory=Event, init=False)
    calibration_generation: int = field(default=0, init=False)
    tracking_reset: Event = field(default_factory=Event, init=False)
    latest: VisionSnapshot = field(default_factory=lambda: VisionSnapshot(
        aims=MappingProxyType({}), confidence=MappingProxyType({})), init=False)
    capture: object = field(default=None, init=False)
    capture_thread: Thread | None = field(default=None, init=False)
    process_thread: Thread | None = field(default=None, init=False)
    frame: object = field(default=None, init=False)
    identity: int | None = field(default=None, init=False)
    identity_ready: float = field(default=0.0, init=False)
    matrix: np.ndarray | None = field(default=None, init=False)
    camera_shape: tuple | None = field(default=None, init=False)
    tracker: LaserTracker = field(default_factory=LaserTracker, init=False)
    wall_filter: WallFilter = field(default_factory=WallFilter, init=False)
    walls: np.ndarray | None = field(default=None, init=False)
    last_wall_time: float = field(default=0.0, init=False)
    wall_resume: float = field(default=0.0, init=False)
    calibration_warning: str = field(default="", init=False)
    calibration_canvas: np.ndarray | None = field(default=None, init=False)
    capture_error: str = field(default="", init=False)

    def dimensions(self):
        display = self.config.get("display", {})
        return int(display.get("width", 1280)), int(display.get("height", 720))

    def start(self):
        if self.capture_thread is not None and self.capture_thread.is_alive():
            return
        self.stopping.clear()
        self.frame_ready.clear()
        self.frame = None
        camera = self.config.get("camera", {})
        self.tracker = LaserTracker(float(camera.get("stale_seconds", 0.5)))
        self.wall_filter = WallFilter(int(camera.get("wall_persistence", 3)))
        self.process_thread = Thread(target=self.process_loop, name="vision-process", daemon=True)
        self.capture_thread = Thread(target=self.capture_loop, name="vision-capture", daemon=True)
        self.process_thread.start()
        self.capture_thread.start()

    def stop(self):
        self.stopping.set()
        self.frame_ready.set()
        for thread in (self.capture_thread, self.process_thread):
            if thread is not None:
                thread.join(timeout=1.5)
        # Capture owns release; do not release a VideoCapture concurrently with read().

    def snapshot(self):
        with self.lock:
            snapshot = self.latest
        stale = float(self.config.get("camera", {}).get("stale_seconds", 0.5))
        if snapshot.timestamp and monotonic() - snapshot.timestamp > stale:
            return VisionSnapshot(snapshot.timestamp, MappingProxyType({}), MappingProxyType({}),
                                  snapshot.walls, snapshot.preview, snapshot.calibrated,
                                  "Camera frames are stale; aiming paused")
        return snapshot

    def set_identity(self, player_id, ready_since):
        if player_id is not None and player_id not in (1, 2, 3):
            raise ValueError("Player identity must be 1, 2, 3, or None")
        with self.lock:
            self.identity = player_id
            self.identity_ready = float(ready_since)

    def begin_calibration(self):
        with self.lock:
            self.calibration_generation += 1
            self.calibration_requested.set()
            previous = self.latest
            self.latest = VisionSnapshot(previous.timestamp, MappingProxyType({}), MappingProxyType({}),
                                         previous.walls, previous.preview, False, "Show all four calibration markers")

    def cancel_calibration(self):
        """Cancel pending detection, retain the usable map, and let projected markers clear."""
        with self.lock:
            self.calibration_generation += 1
            self.calibration_requested.clear()
            self.wall_resume = max(self.wall_resume, monotonic() + 0.5)
            self.tracking_reset.set()
            previous = self.latest
            calibrated = self.matrix is not None
            error = self.capture_error or self.calibration_warning or ("" if calibrated else "Calibration required")
            self.latest = VisionSnapshot(previous.timestamp, MappingProxyType({}), MappingProxyType({}),
                                         self.walls, previous.preview, calibrated, error)

    def draw_calibration(self, surface):
        import pygame
        width, height = self.dimensions()
        if self.calibration_canvas is None:
            self.calibration_canvas = calibration_image(width, height)
        picture = pygame.surfarray.make_surface(self.calibration_canvas.swapaxes(0, 1))
        if surface.get_size() != (width, height):
            picture = pygame.transform.scale(picture, surface.get_size())
        surface.blit(picture, (0, 0))

    def report_error(self, message, camera_failure=False, generation=None):
        with self.lock:
            if generation is not None and generation != self.calibration_generation:
                return
            if camera_failure:
                self.capture_error = message
                self.frame = None
                self.tracking_reset.set()
            previous = self.latest
            self.latest = VisionSnapshot(previous.timestamp, MappingProxyType({}), MappingProxyType({}),
                                         previous.walls, previous.preview, previous.calibrated, message)

    def capture_loop(self):
        camera = self.config.get("camera", {})
        while not self.stopping.is_set():
            capture = None
            try:
                capture = cv2.VideoCapture(camera.get("device", 0), cv2.CAP_V4L2)
                self.capture = capture
                if not capture.isOpened():
                    self.report_error("Camera unavailable; retrying", camera_failure=True)
                else:
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(camera.get("width", 1280)))
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(camera.get("height", 720)))
                    capture.set(cv2.CAP_PROP_FPS, float(camera.get("fps", 30)))
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    while not self.stopping.is_set():
                        okay, frame = capture.read()
                        timestamp = monotonic()
                        if not okay or frame is None:
                            self.report_error("Camera read failed; reconnecting", camera_failure=True)
                            break
                        with self.lock:
                            # Single-slot handoff replaces older unprocessed frames.
                            self.frame = (timestamp, frame)
                            self.capture_error = ""
                        self.frame_ready.set()
            except Exception as error:
                self.report_error(f"Camera failure: {error}", camera_failure=True)
            finally:
                if capture is not None:
                    capture.release()
                self.capture = None
            self.stopping.wait(1.0)

    def process_loop(self):
        while not self.stopping.is_set():
            self.frame_ready.wait(timeout=0.2)
            self.frame_ready.clear()
            with self.lock:
                item = self.frame
                self.frame = None
                identity, ready = self.identity, self.identity_ready
                generation = self.calibration_generation
            if item is None or self.stopping.is_set():
                continue
            timestamp, frame = item
            try:
                snapshot = self.process_frame(frame, timestamp, identity, ready)
                with self.lock:
                    if generation != self.calibration_generation:
                        continue
                    if self.capture_error:
                        snapshot = VisionSnapshot(snapshot.timestamp, MappingProxyType({}), MappingProxyType({}),
                                                  snapshot.walls, snapshot.preview, snapshot.calibrated, self.capture_error)
                    elif self.calibration_requested.is_set() and snapshot.calibrated:
                        snapshot = VisionSnapshot(snapshot.timestamp, MappingProxyType({}), MappingProxyType({}),
                                                  snapshot.walls, snapshot.preview, False, "Show all four calibration markers")
                    self.latest = snapshot
            except Exception as error:
                self.report_error(f"Vision failure: {error}", generation=generation)

    def process_frame(self, frame, timestamp, identity=None, ready_since=0.0):
        """Process one observation; also usable synchronously for synthetic checks."""
        camera = self.config.get("camera", {})
        with self.lock:
            generation = self.calibration_generation
            calibrating = self.calibration_requested.is_set()
        if self.tracking_reset.is_set():
            self.tracker.tracks.clear()
            self.tracking_reset.clear()
        width, height = self.dimensions()
        shape = frame.shape[:2]
        if self.camera_shape != shape:
            self.camera_shape = shape
            self.matrix = None
            self.tracker.tracks.clear()
            self.walls = None
            self.wall_filter = WallFilter(int(camera.get("wall_persistence", 3)))
            try:
                self.matrix = load_calibration(camera.get("calibration_file", "calibration.npz"), shape, (height, width))
                self.calibration_warning = ""
            except FileNotFoundError:
                self.calibration_warning = "Calibration required"
            except (OSError, ValueError, KeyError):
                self.calibration_warning = "Saved calibration invalid or size changed; recalibrate"
        preview = readonly(frame)
        if calibrating:
            matrix = find_calibration(frame, width, height)
            with self.lock:
                if generation != self.calibration_generation:
                    return self.latest
                if matrix is None:
                    return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), self.walls,
                                          preview, False, "Show all four calibration markers")
                self.matrix = matrix
                self.tracker.tracks.clear()
                self.wall_filter = WallFilter(int(camera.get("wall_persistence", 3)))
                self.walls = None
                self.last_wall_time = timestamp
                self.wall_resume = timestamp + 0.5
                self.calibration_warning = ""
                try:
                    save_calibration(camera.get("calibration_file", "calibration.npz"), matrix, shape, (height, width))
                except OSError as error:
                    self.calibration_warning = f"Calibration active but could not save: {error}"
                self.calibration_requested.clear()
                return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), None,
                                      preview, True, self.calibration_warning)
        if self.matrix is None:
            return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), None,
                                  preview, False, self.calibration_warning or "Calibration required")
        points = laser_candidates(frame, self.matrix, width, height,
                                  int(camera.get("laser_min_area", 1)), int(camera.get("laser_max_area", 180)))
        aims, confidence = self.tracker.update(points, timestamp, identity, ready_since)
        if timestamp >= self.wall_resume and timestamp - self.last_wall_time >= 1 / max(0.1, float(camera.get("wall_update_hz", 10))):
            warped = cv2.warpPerspective(frame, self.matrix, (width, height), borderValue=(255, 255, 255))
            dark = np.max(warped, axis=2) < int(camera.get("wall_threshold", 75))
            with self.lock:
                if generation != self.calibration_generation:
                    return self.latest
                self.walls = self.wall_filter.update(dark)
                self.last_wall_time = timestamp
        return VisionSnapshot(timestamp, MappingProxyType(aims), MappingProxyType(confidence),
                              self.walls, preview, True, self.calibration_warning)
