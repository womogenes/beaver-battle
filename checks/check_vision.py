"""Run from the repository root: .venv/bin/python -m checks.check_vision."""

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

import cv2
import numpy as np

from beaver_battle.vision import (LaserTracker, Vision, WallFilter, calibration_image,
                                  find_calibration, laser_candidates, load_calibration,
                                  save_calibration)


def check_calibration():
    width, height = 800, 480
    image = calibration_image(width, height)
    original = np.float32([(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)])
    observed = np.float32([(95, 70), (890, 32), (938, 650), (52, 590)])
    projection = cv2.getPerspectiveTransform(original, observed)
    camera = cv2.warpPerspective(image, projection, (1024, 720), borderValue=(190, 190, 190))
    matrix = find_calibration(camera, width, height)
    assert matrix is not None, "Four projected markers must calibrate under perspective"
    targets = np.float32([(400, 240), (100, 100), (700, 400)])
    camera_points = cv2.perspectiveTransform(targets[None], projection)
    recovered = cv2.perspectiveTransform(camera_points, matrix)[0]
    assert np.max(np.linalg.norm(recovered - targets, axis=1)) < 2
    camera[0:260, 0:330] = 190
    assert find_calibration(camera, width, height) is None, "Missing a corner marker must not calibrate"
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "calibration.npz"
        save_calibration(path, matrix, (720, 1024), (height, width))
        assert np.allclose(load_calibration(path, (720, 1024), (height, width)), matrix)
        try:
            load_calibration(path, (480, 640), (height, width))
            raise AssertionError("Changed camera shape accepted stale calibration")
        except ValueError:
            pass


def check_candidates():
    frame = np.full((240, 320, 3), 200, np.uint8)
    cv2.circle(frame, (40, 60), 3, (0, 0, 255), -1)
    cv2.circle(frame, (150, 80), 4, (0, 0, 255), -1)
    cv2.circle(frame, (150, 80), 1, (255, 255, 255), -1)
    cv2.circle(frame, (220, 100), 15, (0, 0, 255), -1)
    cv2.circle(frame, (100, 180), 3, (255, 100, 0), -1)
    transform = np.array([[2., 0., 5.], [0., 2., 10.], [0., 0., 1.]])
    candidates = laser_candidates(frame, transform, 640, 480)
    assert len(candidates) == 2, "Reject large projected red art and non-red bright blobs"
    assert np.allclose(sorted(candidates), [(85, 130), (305, 170)], atol=1)


def check_identity():
    tracker = LaserTracker()
    assert tracker.update([(100, 100)], 1.0)[0] == {}, "Position alone cannot create identity"
    assert tracker.update([(100, 100)], 1.1, 1, 1.2)[0] == {}, "Ignore pre-settle frames"
    assert tracker.update([(100, 100), (200, 100)], 1.21, 1, 1.2)[0] == {}, "Ambiguous ID window"
    assert tracker.update([(100, 100)], 1.22, 1, 1.2)[0] == {1: (100, 100)}
    tracker.update([(300, 100)], 1.24, 2, 1.2)
    tracker.update([(500, 100)], 1.26, 3, 1.2)
    aims, confidence = tracker.update([(505, 100), (305, 100), (105, 100)], 1.30)
    assert aims == {1: (105, 100), 2: (305, 100), 3: (505, 100)}, "Assignment is independent of component order"
    assert len(confidence) == 3
    assert tracker.update([(110, 100), (310, 100), (510, 100)], 1.31, None, 1.4)[0] == {}
    assert tracker.update([], 1.35)[0] == {}
    assert tracker.update([(105, 100), (305, 100), (505, 100)], 1.4)[0] == {}, "Lost identity needs a new ID window"
    assert tracker.update([(110, 100)], 1.41, 1, 1.4)[0] == {1: (110, 100)}

    crossing = LaserTracker()
    crossing.update([(100, 100)], 2.0, 1, 2.0)
    crossing.update([(140, 100)], 2.01, 2, 2.0)
    assert crossing.update([(120, 100)], 2.04)[0] == {}, "Merged dot must not be credited to either player"
    assert crossing.update([(100, 100), (140, 100)], 2.08)[0] == {}, "Separated dots cannot silently swap IDs"
    crossing.update([(140, 100)], 2.1, 1, 2.1)
    crossing.update([(100, 100)], 2.12, 2, 2.12)
    assert crossing.update([(145, 100), (95, 100)], 2.15)[0] == {1: (145, 100), 2: (95, 100)}
    assert crossing.update([(145, 100), (95, 100)], 3.0)[0] == {}, "Stale identities cannot reacquire from motion"


def check_walls():
    walls = WallFilter(3)
    empty = np.zeros((12, 16), bool)
    ink = empty.copy()
    ink[3:9, 7:9] = True
    walls.update(empty)
    assert not walls.update(ink).any()
    walls.update(empty)
    assert not walls.update(ink).any(), "One-frame shadow cannot become a wall"
    walls.update(ink)
    solid = walls.update(ink)
    assert np.array_equal(solid, ink)
    assert not solid.flags.writeable
    assert np.array_equal(walls.update(empty), ink), "One bright frame cannot erase physical ink"
    walls.update(empty)
    assert not walls.update(empty).any(), "Erasure must update live geometry"
    assert np.array_equal(solid, ink), "Published wall arrays must not mutate later"


def check_pipeline():
    width, height = 640, 360
    with TemporaryDirectory() as temporary:
        config = {"display": {"width": width, "height": height},
                  "camera": {"calibration_file": str(Path(temporary) / "calibration.npz"),
                             "wall_persistence": 2, "wall_update_hz": 10}}
        vision = Vision(config)
        blank = np.full((height, width, 3), 220, np.uint8)
        assert not vision.process_frame(blank, 1.0).calibrated
        vision.begin_calibration()
        calibrated = vision.process_frame(calibration_image(width, height), 1.1)
        assert calibrated.calibrated and calibrated.walls is None
        frame = blank.copy()
        frame[150:220, 350:365] = 15
        frame[50:100, 100:200] = (255, 80, 20)
        cv2.circle(frame, (280, 100), 3, (0, 0, 255), -1)
        identified = vision.process_frame(frame, 1.7, 1, 1.65)
        assert np.linalg.norm(np.asarray(identified.aims[1]) - (280, 100)) < 2
        second = vision.process_frame(frame, 1.85)
        assert second.walls[180, 357], "Physical dark line becomes wall"
        assert not second.walls[70, 150], "Bright saturated blue art must not become wall"
        assert not second.preview.flags.writeable and not second.walls.flags.writeable
        try:
            second.aims[1] = (0, 0)
            raise AssertionError("Mutable aim mapping")
        except TypeError:
            pass
        try:
            second.calibrated = False
            raise AssertionError("Mutable snapshot")
        except FrozenInstanceError:
            pass
        vision.latest = second
        assert not vision.snapshot().aims, "Stale camera snapshot must pause aims"
        vision.report_error("synthetic disconnect")
        assert not vision.latest.aims and "disconnect" in vision.latest.error
        loaded = Vision(config)
        assert loaded.process_frame(blank, monotonic()).calibrated


@dataclass
class SyntheticCamera:
    online: Event
    reads: int = 0
    released: bool = False

    def isOpened(self):
        return True

    def set(self, key, value):
        return True

    def read(self):
        sleep(0.003)
        self.reads += 1
        return (True, np.full((120, 160, 3), self.reads % 256, np.uint8)) if self.online.is_set() else (False, None)

    def release(self):
        self.released = True


def await_condition(condition, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if condition():
            return
        sleep(0.01)
    raise AssertionError("Timed out waiting for synthetic capture state")


def check_worker():
    online = Event()
    online.set()
    devices = []
    processed = []
    vision = Vision({"display": {"width": 160, "height": 120},
                     "camera": {"calibration_file": "/tmp/no-synthetic-calibration-required.npz"}})
    process = vision.process_frame

    def open_camera(device, backend):
        camera = SyntheticCamera(online)
        devices.append(camera)
        return camera

    def process_slowly(frame, timestamp, identity, ready_since):
        sleep(0.04)
        processed.append(int(frame[0, 0, 0]))
        return process(frame, timestamp, identity, ready_since)

    with patch("beaver_battle.vision.cv2.VideoCapture", side_effect=open_camera):
        vision.process_frame = process_slowly
        try:
            vision.start()
            await_condition(lambda: len(processed) >= 3)
            assert devices[0].reads > len(processed) * 5
            assert any(b - a > 5 for a, b in zip(processed, processed[1:])), "Processor must skip queued camera frames"
            assert monotonic() - vision.snapshot().timestamp < 0.2
            online.clear()
            await_condition(lambda: "reconnecting" in vision.latest.error)
            sleep(0.08)
            assert "reconnecting" in vision.latest.error, "An in-flight frame cannot hide a camera failure"
            assert devices[0].released
            old_timestamp = vision.latest.timestamp
            online.set()
            await_condition(lambda: len(devices) >= 2 and vision.latest.timestamp > old_timestamp)
            assert "reconnecting" not in vision.latest.error
        finally:
            vision.stop()
    assert all(camera.released for camera in devices)
    assert not vision.capture_thread.is_alive() and not vision.process_thread.is_alive()


check_calibration()
check_candidates()
check_identity()
check_walls()
check_pipeline()
check_worker()
print("Vision checks passed: calibration, laser identity/overlap, walls, immutable/stale snapshots, capture backlog/reconnect/release")
