"""Latest-frame UVC capture, planar calibration, physical walls, and red aims.

Preview arrays are BGR camera pixels. Aims and walls use logical display pixels.
No camera control (exposure, gain, white balance) is changed by this module.
"""

import platform
from collections import deque
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from types import MappingProxyType

import cv2
import numpy as np

from beaver_battle.model import VisionSnapshot


BACKENDS = {"avfoundation": cv2.CAP_AVFOUNDATION, "v4l2": cv2.CAP_V4L2,
            "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}
MARKER_NAMES = {0: "top-left", 1: "top-centre", 2: "top-right", 3: "left", 4: "centre",
                5: "right", 6: "bottom-left", 7: "bottom-centre", 8: "bottom-right"}
MARKER_MINIMUM = 3
MARKER_SPREAD = 0.45
MARKER_CLIP_LIMIT = 8.0
MARKER_TILES = 16
MARKER_MEMORY_SECONDS = 2.0
WALL_THRESHOLD = 75
WALL_CONTRAST = 20
WALL_FAINT = 9
WALL_STROKE = 21
LASER_RED_MIN = 160
LASER_REDNESS = 16
LASER_MERGE = 13
OBSTACLE_THRESHOLD = 40
OBSTACLE_MIN_AREA = 900
PROJECTION_DELAY = 0.12


def capture_backend(name=""):
    """Pick the host's UVC backend; V4L2 is Linux-only and fails to open on macOS."""
    if name:
        if name.lower() not in BACKENDS:
            raise ValueError(f"Unknown camera.backend {name!r}; use one of {sorted(BACKENDS)}")
        return BACKENDS[name.lower()]
    return {"Darwin": cv2.CAP_AVFOUNDATION, "Linux": cv2.CAP_V4L2}.get(platform.system(), cv2.CAP_ANY)


def open_capture(config):
    camera = config.get("camera", {})
    capture = cv2.VideoCapture(camera.get("device", 0), capture_backend(camera.get("backend", "")))
    if capture.isOpened():
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(camera.get("width", 1280)))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(camera.get("height", 720)))
        capture.set(cv2.CAP_PROP_FPS, float(camera.get("fps", 30)))
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def probe_cameras(config, count=5):
    """Open each index briefly so the operator can find the board camera, not the built-in one."""
    results = []
    for index in range(count):
        probe = dict(config, camera=dict(config.get("camera", {}), device=index))
        capture = open_capture(probe)
        okay, frame = capture.read() if capture.isOpened() else (False, None)
        capture.release()
        results.append((index, None if not okay or frame is None else frame.shape[1::-1]))
    return results


def readonly(array):
    result = array.copy()
    result.setflags(write=False)
    return result


def marker_layout(width, height):
    """Nine markers on a three by three grid, so covering one does not stop calibration."""
    size = max(24, min(width, height) // 7)
    margin = max(12, size // 3)
    xs = (margin, (width - size) // 2, width - margin - size)
    ys = (margin, (height - size) // 2, height - margin - size)
    origins = [(x, y) for y in ys for x in xs]
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


def marker_image(frame, clip_limit=MARKER_CLIP_LIMIT, tiles=MARKER_TILES):
    """Equalize locally. A projector on a lit whiteboard can separate its own black from its own
    white by only a few gray levels, which plain adaptive thresholding cannot recover."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if clip_limit <= 0:
        return gray
    size = max(1, int(tiles))
    return cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(size, size)).apply(gray)


def detect_markers(frame, clip_limit=MARKER_CLIP_LIMIT, tiles=MARKER_TILES):
    """Return unambiguous marker corners by ID; a duplicated ID is reported as absent."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(dictionary, parameters)
    corners, ids, rejected = detector.detectMarkers(marker_image(frame, clip_limit, tiles))
    if ids is None:
        return {}
    numbers = list(ids.flatten())
    return {int(number): points.reshape(4, 2) for number, points in zip(numbers, corners)
            if numbers.count(number) == 1}


def marker_message(found):
    """Say what is blocked, on the board, where whoever can fix it is standing."""
    layout = sorted(MARKER_NAMES)
    missing = [MARKER_NAMES[number] for number in layout if number not in found]
    if len(found) >= MARKER_MINIMUM:
        return (f"{len(found)} of {len(layout)} markers seen but the mapping was rejected; "
                "spread them wider, reduce glare, and keep everything still")
    return (f"Calibration needs {MARKER_MINIMUM} of {len(layout)} markers and sees {len(found)}. "
            f"Erase ink or clear objects off: {', '.join(missing[:4])}")


def homography(found, width, height):
    """Map camera pixels to logical display pixels from whatever markers are readable.

    Each marker contributes four corners of a known square, so a mapping needs far fewer
    than all nine. What it does need is reach: markers bunched into one part of the board
    fix that part well and extrapolate badly across the rest, which no reprojection check
    on the markers themselves would catch. Hence a span requirement as well as a count.
    """
    layout = marker_layout(width, height)
    usable = [number for number in sorted(layout) if number in found]
    if len(usable) < MARKER_MINIMUM:
        return None
    centers = np.float32([found[number].mean(axis=0) for number in usable])
    targets = np.float32([layout[number].mean(axis=0) for number in usable])
    span = targets.max(axis=0) - targets.min(axis=0)
    if span[0] < MARKER_SPREAD * width or span[1] < MARKER_SPREAD * height:
        return None
    source = np.concatenate([found[number] for number in usable])
    destination = np.concatenate([layout[number] for number in usable])
    # A wide lens does not photograph a plane as a plane, so markers spread across the
    # frame cannot all sit on one homography. Measured on the mounted Arducam, a good
    # mapping still leaves a median corner error near 1.3 px and a worst corner near 7.
    # These limits are here to reject a mapping built from mismatched markers, which is
    # wrong by tens of pixels, not to demand a fit the optics cannot deliver.
    matrix, inliers = cv2.findHomography(source, destination, cv2.RANSAC, 6.0)
    if matrix is None or inliers is None or int(inliers.sum()) < max(12, len(source) * 3 // 5):
        return None
    if not np.isfinite(matrix).all():
        return None
    error = np.linalg.norm(cv2.perspectiveTransform(source[None], matrix)[0] - destination, axis=1)
    if np.median(error) > 4.0 or error.max() > 15.0:
        return None
    if abs(np.linalg.det(matrix)) < 1e-9:
        return None
    return matrix


def find_calibration(frame, width, height, clip_limit=MARKER_CLIP_LIMIT, tiles=MARKER_TILES):
    return homography(detect_markers(frame, clip_limit, tiles), width, height)


@dataclass
class MarkerMemory:
    """Gather marker sightings over a short window.

    A dim projector on a lit whiteboard can leave individual markers below the detector's
    threshold on any single frame while the rig itself is motionless. Remembering each
    marker's most recent corners lets one calibration succeed from several frames. A rig
    that actually moves during the window mixes poses, which the reprojection limit in
    homography() rejects rather than accepting a wrong mapping.
    """

    memory_seconds: float = MARKER_MEMORY_SECONDS
    corners: dict = field(default_factory=dict)

    def clear(self):
        self.corners = {}

    def update(self, found, timestamp):
        for number, points in found.items():
            self.corners[number] = (timestamp, points)
        self.corners = {number: entry for number, entry in self.corners.items()
                        if 0 <= timestamp - entry[0] <= self.memory_seconds}
        return {number: entry[1] for number, entry in self.corners.items()}


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


def laser_candidates(frame, matrix, width, height, min_area=1, max_area=180,
                     red_min=LASER_RED_MIN, redness_min=LASER_REDNESS, merge=LASER_MERGE):
    """Find red components before warping, preserving small physical laser spots.

    Redness is the red channel less the stronger of the other two, which is what separates
    a laser spot from a merely bright one. How high it can go is set by the surface, not
    the laser: on a whiteboard reading about 160 the dot drives red to 253, essentially
    clipped, so redness cannot exceed roughly a hundred and in practice measured 29 to 58.
    A threshold of 60 therefore rejected every real dot while the wooden trim and the pens
    in the tray, at redness near 100, passed. The board itself is neutral -- measured
    median -8 and never above 8 -- so the margin below the dot is what there is to use.
    """
    blue, green, red = cv2.split(frame)
    redness = red.astype(np.int16) - np.maximum(blue, green).astype(np.int16)
    mask = ((red >= int(red_min)) & (redness >= int(redness_min))).astype(np.uint8)
    if merge and int(merge) > 1:
        # One dot arrives as a bright core with speckle around it, and those pieces sit a
        # few pixels apart: measured, every frame carrying more than one piece had them
        # within 18 px, which the tracker reads as a merged, unusable spot. Joining them
        # under the tracker's own ambiguity distance costs nothing, since anything closer
        # than that could never have been told apart anyway.
        span = int(merge) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (span, span)))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    points = [centers[index] for index in range(1, count)
              if min_area <= stats[index, cv2.CC_STAT_AREA] <= max_area]
    if not points:
        return []
    transformed = cv2.perspectiveTransform(np.float32(points)[None], matrix)[0]
    return [tuple(map(float, point)) for point in transformed
            if np.isfinite(point).all() and 0 <= point[0] < width and 0 <= point[1] < height]


def grow(seed, region):
    """Keep every part of region that touches a seed, and nothing that does not."""
    count, labels = cv2.connectedComponents(region.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros(region.shape, bool)
    keep = np.zeros(count, bool)
    keep[labels[seed]] = True
    keep[0] = False
    return keep[labels]


def ink_mask(warped, threshold=WALL_THRESHOLD, contrast=WALL_CONTRAST, stroke=WALL_STROKE,
             faint=WALL_FAINT):
    """Mark physical ink: pixels darker than the board immediately around them.

    An absolute cutoff alone cannot find a drawing on a projected surface. Measured on the
    mounted rig, glare moves the bare board between about 158 and 212 while a black stroke
    photographs near 150 once the lens blurs it against a lit background, so no single
    cutoff separates them. A black-hat transform asks the question that survives the
    gradient instead: is this pixel darker than the board beside it. The absolute cutoff
    stays as an OR so that genuinely dark regions wider than the kernel still register.

    The red channel alone is read, which decides what a pen may be drawn in. A red laser
    dot *raises* red, so no ink that lowers red can be mistaken for one: black, green,
    blue, purple and brown all absorb red and are safe to detect. Ink that instead keeps
    red high and lowers green and blue -- red, orange, pink, magenta -- produces exactly a
    laser's signature and is deliberately left invisible rather than made ambiguous.
    Measured on the bench, moving from the strongest channel to red took green ink from
    413 to 973 pixels and blue from 53 to 255 while lowering board noise from 29 to 11.
    """
    gray = warped if warped.ndim == 2 else warped[:, :, 2]
    size = max(3, int(stroke) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    relief = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    solid = gray < int(threshold)
    strong = (relief >= int(contrast)) | solid
    if faint is None or int(faint) >= int(contrast):
        return strong
    # One threshold breaks a hand-drawn stroke into dashes wherever the pen ran dry, and a
    # barrier with gaps wider than a hull is not a barrier. Let confident ink recruit the
    # faint ink joined to it, while faint marks standing alone are still rejected.
    weak = (relief >= int(faint)) | solid
    return grow(strong, weak)


def scan_board(warped, width, height, threshold=WALL_THRESHOLD, contrast=WALL_CONTRAST,
               stroke=WALL_STROKE, faint=WALL_FAINT):
    """Read the drawing from the calibration screen, whose bright field is known.

    This is the cleanest observation of physical ink the rig ever gets: the projector is
    showing a flat white field rather than game art, so anything locally dark is on the
    board. The four projected markers are dark by construction and are excluded.
    """
    mask = ink_mask(warped, threshold, contrast, stroke, faint)
    margin = max(3, int(stroke))
    for corners in marker_layout(width, height).values():
        left, top = corners[0].astype(int) - margin
        right, bottom = corners[2].astype(int) + margin
        mask[max(0, top):max(0, bottom), max(0, left):max(0, right)] = False
    return mask


def projector_gain(reference, width, height, ink):
    """How many camera levels the projector itself is worth, per channel.

    The calibration screen conveniently shows both extremes at once: a white field and
    four black markers. Their difference is exactly the projector's contribution, which is
    what has to be predicted away before a shadow can be told apart from dark artwork.
    """
    dark = np.zeros((height, width), bool)
    for corners in marker_layout(width, height).values():
        inset = max(2, min(width, height) // 90)
        left, top = corners[0].astype(int) + inset
        right, bottom = corners[2].astype(int) - inset
        dark[max(0, top):max(0, bottom), max(0, left):max(0, right)] = True
    lit = ~dark & ~ink
    if dark.sum() < 64 or lit.sum() < 64:
        return None
    gain = np.median(reference[lit], 0) - np.median(reference[dark], 0)
    return gain.astype(np.float32) if np.all(gain > 1) else None


def expected_board(reference, gain, canvas):
    """What the board should look like given the frame currently being projected onto it."""
    return reference - gain * (1.0 - canvas.astype(np.float32) / 255.0)


def under_white(warped, gain, canvas):
    """Undo the projection, recovering the board as it would look under a flat white field.

    Ink is read from the red channel, and cyan or blue artwork is low in red for exactly
    the same reason blue ink is. Adding back the light the projector withheld removes the
    artwork and leaves physical marks, wherever they were drawn and whenever.
    """
    restored = warped.astype(np.float32) + gain * (1.0 - canvas.astype(np.float32) / 255.0)
    return np.clip(restored, 0, 255).astype(np.uint8)


def obstacles(warped, reference, gain, canvas, threshold=OBSTACLE_THRESHOLD,
              min_area=OBSTACLE_MIN_AREA, blur=3.0):
    """Find hands, shadows and placed objects: the board is darker than the projection predicts.

    Absolute darkness cannot answer this. A hand blocking the beam and a deliberately dark
    sprite look identical to the camera, and on a lit whiteboard the pastel palette moves
    individual channels further than a hand does. Subtracting the predicted image removes
    the artwork and leaves only light that something physically took away.

    Both sides are blurred because projector latency and sub-pixel warp error otherwise
    ring at every moving edge, and small components are dropped so that laser dots and
    sensor noise cannot become walls.
    """
    expected = cv2.GaussianBlur(expected_board(reference, gain, canvas), (0, 0), blur)
    observed = cv2.GaussianBlur(warped.astype(np.float32), (0, 0), blur)
    deficit = np.max(expected - observed, axis=2)
    mask = (deficit > float(threshold)).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros(count, bool)
    for index in range(1, count):
        keep[index] = stats[index, cv2.CC_STAT_AREA] >= int(min_area)
    return keep[labels]


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

    def seed(self, mask):
        """Adopt a scan outright; a calibration board read needs no repeat confirmation."""
        limit = max(1, min(127, int(self.persistence)))
        self.evidence = np.where(mask, limit, -limit).astype(np.int16)
        self.mask = np.array(mask, dtype=bool)
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
    marker_memory: MarkerMemory = field(default_factory=MarkerMemory, init=False)
    board_reference: np.ndarray | None = field(default=None, init=False)
    survey_votes: np.ndarray | None = field(default=None, init=False)
    survey_samples: int = field(default=0, init=False)
    survey_until: float = field(default=0.0, init=False)
    board_gain: np.ndarray | None = field(default=None, init=False)
    projections: deque = field(default_factory=lambda: deque(maxlen=16), init=False)
    walls: np.ndarray | None = field(default=None, init=False)
    last_wall_time: float = field(default=0.0, init=False)
    wall_resume: float = field(default=0.0, init=False)
    calibration_warning: str = field(default="", init=False)
    calibration_canvas: np.ndarray | None = field(default=None, init=False)
    capture_error: str = field(default="", init=False)

    def begin_survey(self, seconds=3.0):
        """Read the board for a while and keep what was there most of the time.

        Deciding the geometry afresh ten times a second makes a marginal stroke flicker,
        and one pixel at a repair takes a whole enclosure with it, so the board appeared
        to shade itself in and out while nobody touched it. A vote over a few seconds
        settles that before anyone is playing on it.
        """
        with self.lock:
            self.survey_votes = None
            self.survey_samples = 0
            self.survey_until = monotonic() + max(0.1, float(seconds))

    def surveying(self):
        with self.lock:
            return self.survey_until > 0.0

    def survey_progress(self):
        with self.lock:
            if self.survey_until <= 0.0:
                return 1.0
            left = self.survey_until - monotonic()
            total = max(0.1, float(self.config.get("camera", {}).get("survey_seconds", 3.0)))
            return float(min(1.0, max(0.0, 1.0 - left / total)))

    def set_projection(self, canvas, timestamp=None):
        """Hand over the frame just drawn, as an (h, w, 3) RGB array in logical pixels.

        Without it the worker cannot tell a shadow from dark artwork and reports drawn ink
        only. Calling it is optional; the game loop should, headless checks need not.
        """
        canvas = np.asarray(canvas)
        width, height = self.dimensions()
        if canvas.shape != (height, width, 3):
            raise ValueError(f"Projection must be {height}x{width}x3 in logical pixels")
        with self.lock:
            self.projections.append((monotonic() if timestamp is None else float(timestamp),
                                     canvas[:, :, ::-1].copy()))

    def projection_for(self, timestamp):
        """The frame that was actually on the board when this camera frame was taken."""
        delay = float(self.config.get("camera", {}).get("projection_delay", PROJECTION_DELAY))
        with self.lock:
            if not self.projections:
                return None
            wanted = timestamp - delay
            return min(self.projections, key=lambda item: abs(item[0] - wanted))[1]

    def obstacle_settings(self):
        camera = self.config.get("camera", {})
        return (float(camera.get("obstacle_threshold", OBSTACLE_THRESHOLD)),
                int(camera.get("obstacle_min_area", OBSTACLE_MIN_AREA)))

    def ink_settings(self):
        camera = self.config.get("camera", {})
        return (int(camera.get("wall_threshold", WALL_THRESHOLD)),
                int(camera.get("wall_contrast", WALL_CONTRAST)),
                int(camera.get("wall_stroke", WALL_STROKE)),
                int(camera.get("wall_faint", WALL_FAINT)))

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
            self.marker_memory.clear()
            self.calibration_requested.set()
            previous = self.latest
            self.latest = VisionSnapshot(previous.timestamp, MappingProxyType({}), MappingProxyType({}),
                                         previous.walls, previous.preview, False, "Show all four calibration markers")

    def cancel_calibration(self):
        """Cancel pending detection, retain the usable map, and let projected markers clear."""
        with self.lock:
            self.calibration_generation += 1
            self.marker_memory.clear()
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
                capture = open_capture(self.config)
                self.capture = capture
                if not capture.isOpened():
                    self.report_error(f"Camera {camera.get('device', 0)} unavailable; retrying", camera_failure=True)
                else:
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
            clip = float(camera.get("marker_clip_limit", MARKER_CLIP_LIMIT))
            tiles = int(camera.get("marker_tiles", MARKER_TILES))
            self.marker_memory.memory_seconds = float(camera.get("marker_memory_seconds", MARKER_MEMORY_SECONDS))
            found = self.marker_memory.update(detect_markers(frame, clip, tiles), timestamp)
            matrix = homography(found, width, height)
            with self.lock:
                if generation != self.calibration_generation:
                    return self.latest
                if matrix is None:
                    return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), self.walls,
                                          preview, False, marker_message(found))
                self.matrix = matrix
                self.marker_memory.clear()
                self.tracker.tracks.clear()
                self.wall_filter = WallFilter(int(camera.get("wall_persistence", 3)))
                scan = cv2.warpPerspective(frame, matrix, (width, height), borderValue=(255, 255, 255))
                ink = scan_board(scan, width, height, *self.ink_settings())
                self.walls = self.wall_filter.seed(ink)
                self.board_reference = scan.astype(np.float32)
                self.board_gain = projector_gain(self.board_reference, width, height, ink)
                self.projections.clear()
                self.last_wall_time = timestamp
                self.wall_resume = timestamp + 0.5
                self.calibration_warning = ""
                try:
                    save_calibration(camera.get("calibration_file", "calibration.npz"), matrix, shape, (height, width))
                except OSError as error:
                    self.calibration_warning = f"Calibration active but could not save: {error}"
                self.survey_until = 0.0
                self.survey_votes = None
                self.calibration_requested.clear()
                return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), self.walls,
                                      preview, True, self.calibration_warning)
        if self.matrix is None:
            return VisionSnapshot(timestamp, MappingProxyType({}), MappingProxyType({}), None,
                                  preview, False, self.calibration_warning or "Calibration required")
        points = laser_candidates(frame, self.matrix, width, height,
                                  int(camera.get("laser_min_area", 1)), int(camera.get("laser_max_area", 180)),
                                  int(camera.get("laser_red_min", LASER_RED_MIN)),
                                  int(camera.get("laser_redness", LASER_REDNESS)),
                                  int(camera.get("laser_merge", LASER_MERGE)))
        aims, confidence = self.tracker.update(points, timestamp, identity, ready_since)
        rate = float(camera.get("wall_update_hz", 10))
        # A zero rate keeps the calibration board scan and stops re-reading under game art.
        if rate > 0 and timestamp >= self.wall_resume and timestamp - self.last_wall_time >= 1 / rate:
            warped = cv2.warpPerspective(frame, self.matrix, (width, height), borderValue=(255, 255, 255))
            canvas = self.projection_for(timestamp)
            predicted = canvas is not None and self.board_reference is not None and self.board_gain is not None
            surface = under_white(warped, self.board_gain, canvas) if predicted else warped
            dark = ink_mask(surface, *self.ink_settings())
            if predicted:
                dark = dark | obstacles(warped, self.board_reference, self.board_gain,
                                        canvas, *self.obstacle_settings())
            with self.lock:
                if generation != self.calibration_generation:
                    return self.latest
                if self.survey_until > 0.0:
                    # A vote across the whole window, not a verdict per frame.
                    if self.survey_votes is None or self.survey_votes.shape != dark.shape:
                        self.survey_votes = np.zeros(dark.shape, np.int32)
                        self.survey_samples = 0
                    self.survey_votes += dark
                    self.survey_samples += 1
                    if timestamp >= self.survey_until and self.survey_samples >= 3:
                        share = float(self.config.get("camera", {}).get("survey_share", 0.45))
                        settled = self.survey_votes >= max(1, int(self.survey_samples * share))
                        self.walls = self.wall_filter.seed(settled)
                        self.survey_until = 0.0
                        self.survey_votes = None
                    elif self.walls is None:
                        self.walls = self.wall_filter.update(dark)
                else:
                    self.walls = self.wall_filter.update(dark)
                self.last_wall_time = timestamp
        return VisionSnapshot(timestamp, MappingProxyType(aims), MappingProxyType(confidence),
                              self.walls, preview, True, self.calibration_warning)
