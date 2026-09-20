"""ESP-NOW gate reports identify dots without command acknowledgements."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beaver_battle.vision import Vision


def vision_for_check():
    return Vision({"camera": {"identity_settle": 0.08, "stale_seconds": 0.5}})


def observe(vision, points, timestamp):
    return vision.telemetry_aims(points, timestamp)[0]


def check_acquisition():
    vision = vision_for_check()
    vision.set_laser_states({1: True, 2: False}, 1.0)
    assert observe(vision, [(100, 100)], 1.04) == {}, "Wait for optical settling"
    assert observe(vision, [(100, 100)], 1.1) == {1: (100, 100)}
    vision.set_laser_states({1: True, 2: True}, 1.11)
    assert observe(vision, [(105, 100), (400, 100)], 1.14) == {1: (105, 100)}, "A known steady dot continues while the other settles"
    assert vision.tracker.tracks[1].valid, "A transition preserves a continuously lit identity"
    assert observe(vision, [(400, 100), (105, 100)], 1.2) == {1: (105, 100), 2: (400, 100)}
    assert observe(vision, [(110, 100), (405, 100)], 1.23) == {1: (110, 100), 2: (405, 100)}
    vision.set_laser_states({1: True, 2: False}, 1.24)
    assert observe(vision, [(115, 100)], 1.26) == {1: (115, 100)}
    assert not vision.tracker.tracks[2].valid
    assert observe(vision, [(115, 100)], 1.34) == {1: (115, 100)}
    vision.set_laser_states({1: False, 2: False}, 1.35)
    assert observe(vision, [(115, 100)], 1.44) == {}
    assert not any(track.valid for track in vision.tracker.tracks.values())


def check_ambiguity():
    vision = vision_for_check()
    vision.set_laser_states({1: True, 2: True}, 1.0)
    assert observe(vision, [(100, 100), (400, 100)], 1.1) == {}, "Spatial order is not identity"
    vision.set_laser_states({1: True, 2: False}, 1.2)
    assert observe(vision, [(100, 100), (400, 100)], 1.3) == {}, "Solo report with multiple dots is ambiguous"
    assert observe(vision, [(100, 100)], 1.31) == {1: (100, 100)}
    vision.set_laser_states({1: True, 2: True}, 1.32)
    assert observe(vision, [(105, 100)], 1.42) == {1: (105, 100)}, "A weak/missing second dot must not get guessed"
    assert 2 not in vision.tracker.tracks
    assert observe(vision, [(107, 100), (116, 100)], 1.45) == {}, "Unresolved neighboring dots invalidate identity"
    assert observe(vision, [(100, 100), (400, 100)], 1.48) == {}, "No reacquisition after ambiguity without a solo window"
    vision.set_laser_states({1: True, 2: False}, 1.5)
    observe(vision, [(100, 100)], 1.6)
    vision.set_laser_states({1: True, 2: True}, 1.61)
    assert len(observe(vision, [(100, 100), (160, 100)], 1.71)) == 2
    assert observe(vision, [(130, 100)], 1.74) == {}, "Merged known dots must not select either player"
    assert observe(vision, [(100, 100), (160, 100)], 1.77) == {}


def check_timing():
    vision = vision_for_check()
    vision.set_laser_states({1: True}, 2.0)
    assert observe(vision, [(100, 100)], 1.9) == {}, "Future reports cannot label old frames"
    assert observe(vision, [(100, 100)], 2.1) == {1: (100, 100)}
    vision.set_laser_states({2: True}, 3.0)
    assert observe(vision, [(100, 100)], 2.2) == {1: (100, 100)}, "Use the report at capture time"
    assert observe(vision, [(100, 100)], 2.6) == {}, "Expired telemetry pauses aiming"
    assert not vision.tracker.tracks[1].valid
    assert observe(vision, [(100, 100)], 3.1) == {2: (100, 100)}
    vision.set_laser_states({2: True}, 4.0)
    assert observe(vision, [(100, 100)], 4.01) == {}, "A telemetry gap restarts settling"
    assert observe(vision, [(100, 100)], 4.1) == {2: (100, 100)}
    vision.set_laser_states({2: False}, 4.11)
    vision.set_laser_states({1: True, 2: True}, 4.12)
    assert observe(vision, [(100, 100), (400, 100)], 4.22) == {}, "Even a dark interval between frames invalidates identity"
    vision.set_laser_states({1: True}, 4.0)
    assert vision.laser_history[-1][0] == 4.12, "Ignore out-of-order reports"
    for index in range(300):
        vision.set_laser_states({1: True}, 5 + index / 100)
    assert len(vision.laser_history) == 256


def check_pipeline():
    with TemporaryDirectory() as temporary:
        vision = Vision({"display": {"width": 320, "height": 240},
                         "camera": {"calibration_file": str(Path(temporary) / "none.npz"),
                                    "wall_update_hz": 0}})
        frame = np.full((240, 320, 3), 200, np.uint8)
        cv2.circle(frame, (100, 100), 3, (0, 0, 255), -1)
        vision.camera_shape = frame.shape[:2]
        vision.matrix = np.eye(3)
        assert 1 in vision.process_frame(frame, 1.0, 1).aims, "Legacy acknowledgment API remains available"
        vision.set_laser_states({1: False, 2: True}, 1.1)
        result = vision.process_frame(frame, 1.2, 1, 999)
        assert set(result.aims) == {2}, "Telemetry mode replaces acknowledgment gating only once enabled"


check_acquisition()
check_ambiguity()
check_timing()
check_pipeline()
print("Telemetry vision checks passed: solo/second acquisition, weak/merged dots, transitions, stale/future reports, legacy mode")
