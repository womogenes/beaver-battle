"""Run from the repository root: .venv/bin/python -m checks.check_vision."""

import math
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from unittest.mock import patch

import cv2
import numpy as np

from beaver_battle.vision import (LaserTracker, MarkerMemory, Vision, WallFilter,
                                  calibration_image, capture_backend, detect_markers,
                                  expected_board, find_calibration, homography, ink_mask,
                                  laser_candidates, load_calibration, marker_layout,
                                  marker_message, obstacles, projector_gain, save_calibration,
                                  scan_board)


def check_calibration():
    width, height = 800, 480
    image = calibration_image(width, height)
    original = np.float32([(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)])
    observed = np.float32([(95, 70), (890, 32), (938, 650), (52, 590)])
    projection = cv2.getPerspectiveTransform(original, observed)
    camera = cv2.warpPerspective(image, projection, (1024, 720), borderValue=(190, 190, 190))
    matrix = find_calibration(camera, width, height)
    assert matrix is not None, "Projected markers must calibrate under perspective"
    targets = np.float32([(400, 240), (100, 100), (700, 400)])
    camera_points = cv2.perspectiveTransform(targets[None], projection)
    recovered = cv2.perspectiveTransform(camera_points, matrix)[0]
    assert np.max(np.linalg.norm(recovered - targets, axis=1)) < 2
# A projector on a lit whiteboard can leave only a few gray levels between its black and white.
    faint = (camera.astype(np.float32) * 0.03 + 172).astype(np.uint8)
    assert find_calibration(faint, width, height, clip_limit=0) is None, \
        "A faint projection is unreadable without local equalization"
    assert find_calibration(faint, width, height) is not None, "Equalization must recover a faint projection"

    # Ink drawn over a marker destroys it. Losing one, or several, must not stop calibration.
    blocked = camera.copy()
    blocked[0:230, 0:300] = 190
    assert len(detect_markers(blocked)) == 8
    covered = find_calibration(blocked, width, height)
    assert covered is not None, "One blocked marker must not stop calibration"
    assert np.max(np.linalg.norm(cv2.perspectiveTransform(camera_points, covered)[0] - targets, axis=1)) < 3

    # Spread matters more than count: markers bunched together extrapolate badly.
    corner = {number: points for number, points in detect_markers(camera).items() if number in (0, 1, 3, 4)}
    assert homography(corner, width, height) is None, "Clustered markers must not fix the whole board"
    spread = {number: points for number, points in detect_markers(camera).items() if number in (0, 2, 8)}
    assert homography(spread, width, height) is not None, "Three markers reaching across the board suffice"
    assert homography({0: detect_markers(camera)[0]}, width, height) is None, "One marker is never enough"

    lost = camera.copy()
    lost[:, 0:620] = 190
    assert find_calibration(lost, width, height) is None, "Losing a whole side must not calibrate"
    clustered = marker_message(detect_markers(lost))
    assert "spread them wider" in clustered, clustered

    bare = camera.copy()
    bare[:, 140:] = 190
    assert find_calibration(bare, width, height) is None
    sparse = marker_message(detect_markers(bare))
    assert "Erase ink" in sparse and "needs 3 of 9" in sparse, sparse
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "calibration.npz"
        save_calibration(path, matrix, (720, 1024), (height, width))
        assert np.allclose(load_calibration(path, (720, 1024), (height, width)), matrix)
        try:
            load_calibration(path, (480, 640), (height, width))
            raise AssertionError("Changed camera shape accepted stale calibration")
        except ValueError:
            pass


def check_marker_memory():
    """A dim projection can hide a different marker on each frame while the rig sits still."""
    memory = MarkerMemory(memory_seconds=2.0)
    corners = {number: np.float32([(0, 0), (1, 0), (1, 1), (0, 1)]) for number in range(4)}
    assert sorted(memory.update({0: corners[0], 1: corners[1]}, 10.0)) == [0, 1]
    assert sorted(memory.update({2: corners[2]}, 10.5)) == [0, 1, 2]
    assert sorted(memory.update({3: corners[3]}, 11.0)) == [0, 1, 2, 3], "One calibration may span frames"
    assert sorted(memory.update({}, 12.9)) == [3], "Sightings expire after the memory window"
    memory.clear()
    assert memory.update({}, 13.0) == {}, "A new calibration cannot reuse the previous pose"

    width, height = 800, 480
    image = calibration_image(width, height)
    flicker = MarkerMemory()
    found = {}
    for step, number in enumerate((0, 2, 6, 8)):
        single = {number: detect_markers(image)[number]}
        found = flicker.update(single, 20.0 + step * 0.1)
    matrix = homography(found, width, height)
    assert matrix is not None, "Four markers seen one per frame must still calibrate"
    targets = np.float32([(400, 240), (100, 100), (700, 400)])
    recovered = cv2.perspectiveTransform(targets[None], matrix)[0]
    assert np.max(np.linalg.norm(recovered - targets, axis=1)) < 2


def lit_board(width, height):
    """A projected whiteboard: bright, unevenly lit, with a thin drawn circle."""
    glare = np.linspace(210, 158, width, dtype=np.float32)[None, :].repeat(height, 0)
    glare[height // 3:height // 2] += 18
    board = np.clip(glare, 0, 255).astype(np.uint8)
    board = cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)
    cv2.circle(board, (width // 2, height // 2), min(width, height) // 6, (150, 148, 152), 3)
    return cv2.GaussianBlur(board, (3, 3), 0)


def check_ink():
    width, height = 1280, 720
    board = lit_board(width, height)
    # The measured board runs brighter than any usable absolute cutoff, ink included.
    assert not (np.max(board, axis=2) < 75).any(), "This board defeats an absolute cutoff entirely"
    mask = ink_mask(board)
    circle = np.zeros((height, width), np.uint8)
    cv2.circle(circle, (width // 2, height // 2), min(width, height) // 6, 1, 15)
    on_target = int((mask & circle.astype(bool)).sum())
    assert on_target > 1000, f"Local contrast must find the drawing, found {on_target}"
    assert int(mask.sum()) - on_target < on_target // 10, "Glare and gradient must not read as ink"

    bright = cv2.cvtColor(np.full((height, width), 190, np.uint8), cv2.COLOR_GRAY2BGR)
    assert not ink_mask(bright).any(), "A blank bright board holds no ink"
    dark_room = cv2.cvtColor(np.full((height, width), 40, np.uint8), cv2.COLOR_GRAY2BGR)
    assert not ink_mask(dark_room).any(), "A uniformly dim board is not solid ink"
    for level in (8, 15, 40, 100, 200):
        dim = np.full((height, width, 3), level, np.uint8)
        dim[200:400, 400:600] = 0
        detected = ink_mask(dim)
        assert detected[250:350, 450:550].all(), "Wide dark ink remains solid"
        assert detected.mean() < .06, "Blank field must not flood at any brightness"
    red = np.zeros((height, width, 3), np.uint8)
    red[:, :, 2] = 255
    assert not ink_mask(red).any(), "Saturated red stays reserved for laser dots"


def check_ink_colours():
    """What a pen may be drawn in is decided by the laser, not by how dark the ink looks."""
    width, height = 1280, 720
    board = np.full((height, width, 3), 200, np.uint8)
    # Measured bench strokes, as BGR, against a board near 205.
    pens = {"black": (155, 150, 141), "green": (164, 166, 117), "blue": (191, 182, 154),
            "pink": (195, 167, 197), "orange": (170, 180, 194)}
    spots = {}
    for index, (name, colour) in enumerate(pens.items()):
        x = 150 + index * 220
        cv2.circle(board, (x, 300), 60, colour, 4)
        spots[name] = (x, 300)
    mask = ink_mask(board, 75, 20, 21)

    def found(name):
        x, y = spots[name]
        return int(mask[y - 70:y + 70, x - 70:x + 70].sum())

    for name in ("black", "green", "blue"):
        assert found(name) > 300, f"{name} ink absorbs red and must be detected, got {found(name)}"
    for name in ("pink", "orange"):
        assert found(name) < 60, f"{name} ink carries a laser's signature and must stay invisible"

    # The dot the players aim with raises red; it can never be read as ink.
    laser = np.full((height, width, 3), 200, np.uint8)
    cv2.circle(laser, (640, 360), 5, (0, 0, 255), -1)
    assert not ink_mask(laser, 75, 20, 21).any(), "A red laser dot must never become a wall"


def check_scan():
    """Calibration reads the board against the marker screen, whose markers are dark by design."""
    width, height = 1280, 720
    board = lit_board(width, height)
    for corners in marker_layout(width, height).values():
        left, top = corners[0].astype(int)
        right, bottom = corners[2].astype(int)
        board[top:bottom + 1, left:right + 1] = 20
    assert ink_mask(board)[:200, :200].any(), "A projected marker is dark enough to look like ink"
    scanned = scan_board(board, width, height)
    for corners in marker_layout(width, height).values():
        left, top = corners[0].astype(int)
        right, bottom = corners[2].astype(int)
        assert not scanned[top:bottom + 1, left:right + 1].any(), "Markers must not become walls"
    assert scanned.sum() > 1000, "The drawing must survive marker exclusion"

    seeded = WallFilter(3)
    published = seeded.seed(scanned)
    assert np.array_equal(published, scanned), "A board scan is adopted without repeat confirmation"
    assert not published.flags.writeable
    assert np.array_equal(seeded.update(scanned), scanned), "A confirming frame keeps the scan"


def check_obstacles():
    """Hands and shadows are light that went missing, which needs the projected frame to see."""
    width, height = 1280, 720
    gain = np.float32([28, 20, 17])
    board = lit_board(width, height).astype(np.float32)
    reference = board.copy()

    # The game is projecting pastel art with dark outlines and a near-black eye.
    canvas = np.full((height, width, 3), 255, np.uint8)
    cv2.circle(canvas, (900, 300), 120, (240, 218, 180), -1)
    cv2.circle(canvas, (900, 300), 120, (170, 98, 88), 6)
    cv2.circle(canvas, (880, 270), 12, (96, 64, 66), -1)
    cv2.putText(canvas, "PLAYER 1", (120, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (135, 105, 90), 5)
    lit = expected_board(reference, gain, canvas)
    assert not obstacles(lit.astype(np.uint8), reference, gain, canvas).any(), \
        "Projected artwork is predicted away, however dark it is drawn"

    for offset in (-80, -40, 40):
        changed = np.clip(lit + offset, 0, 255).astype(np.uint8)
        assert not obstacles(changed, reference, gain, canvas).any(), 'Ambient change must not flood obstacles'

    # A hand blocks the beam over part of that art and reflects less than the board.
    shadowed = lit.copy()
    hand = np.zeros((height, width), np.uint8)
    cv2.ellipse(hand, (500, 420), (90, 130), 20, 0, 360, 1, -1)
    shadowed[hand.astype(bool)] -= gain            # the projector's light never lands
    shadowed[hand.astype(bool)] -= np.float32([30, 22, 6])   # and skin is not a whiteboard
    found = obstacles(np.clip(shadowed, 0, 255).astype(np.uint8), reference, gain, canvas)
    covered = float((found & hand.astype(bool)).sum()) / float(hand.sum())
    assert covered > 0.8, f"A hand over the beam must become solid, covered {covered:.2f}"
    assert int(found.sum()) - int((found & hand.astype(bool)).sum()) < hand.sum() // 5

    # Erasing ink makes the board brighter than predicted, which is not an obstacle.
    brighter = np.clip(lit + 60, 0, 255).astype(np.uint8)
    assert not obstacles(brighter, reference, gain, canvas).any(), "Erasure must not add walls"

    # A laser dot is small, and small things never become walls.
    dot = lit.copy()
    cv2.circle(dot, (700, 500), 4, (0, 0, 0), -1)
    assert not obstacles(np.clip(dot, 0, 255).astype(np.uint8), reference, gain, canvas).any()

    reading = np.full((height, width, 3), 200, np.uint8)
    for corners in marker_layout(width, height).values():
        left, top = corners[0].astype(int)
        right, bottom = corners[2].astype(int)
        reading[top:bottom + 1, left:right + 1] = 200 - gain.astype(np.uint8)
    measured = projector_gain(reading.astype(np.float32), width, height,
                              np.zeros((height, width), bool))
    assert measured is not None and np.allclose(measured, gain, atol=2), measured
    flat = np.full((height, width, 3), 200, np.float32)
    assert projector_gain(flat, width, height, np.zeros((height, width), bool)) is None, \
        "A screen with no visible markers cannot measure the projector"


def check_projection_handover():
    vision = Vision({"display": {"width": 64, "height": 48},
                     "camera": {"calibration_file": "/tmp/no-calibration.npz", "projection_delay": 0.5}})
    try:
        vision.set_projection(np.zeros((10, 10, 3), np.uint8))
        raise AssertionError("Projection size is not checked")
    except ValueError:
        pass
    assert vision.projection_for(5.0) is None, "No projection published yet"
    for index, moment in enumerate((2.0, 2.5, 3.0)):
        vision.set_projection(np.full((48, 64, 3), index * 10, np.uint8), moment)
    # A frame captured at 3.0 saw what was on the board half a second earlier, not the newest draw.
    assert int(vision.projection_for(3.0)[0, 0, 0]) == 10, "Projection delay must be applied"
    assert int(vision.projection_for(3.6)[0, 0, 0]) == 20
    rgb = np.zeros((48, 64, 3), np.uint8)
    rgb[:, :, 0] = 255
    vision.set_projection(rgb, 9.0)
    assert int(vision.projection_for(9.1)[0, 0, 2]) == 255, "Projection arrives as RGB, stored as BGR"


def check_backend():
    assert capture_backend("v4l2") == cv2.CAP_V4L2
    assert capture_backend("AVFoundation") == cv2.CAP_AVFOUNDATION, "Backend names are case-insensitive"
    try:
        capture_backend("videotoaster")
        raise AssertionError("Unknown camera.backend name accepted")
    except ValueError:
        pass
    with patch("beaver_battle.vision.platform.system", return_value="Darwin"):
        assert capture_backend() == cv2.CAP_AVFOUNDATION, "macOS cannot open the Linux-only V4L2 backend"
    with patch("beaver_battle.vision.platform.system", return_value="Linux"):
        assert capture_backend() == cv2.CAP_V4L2
    with patch("beaver_battle.vision.platform.system", return_value="Haiku"):
        assert capture_backend() == cv2.CAP_ANY


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


def check_laser_on_a_whiteboard():
    """The dot as the camera actually sees it on a lit whiteboard, not as one imagines it.

    Measured on the bench: the board is neutral, redness never above 8, while the dot
    drives red to 253 and reaches redness 29 to 58. It cannot do better, because how far
    red can rise above the other channels is set by how bright the surface already is.
    A threshold of 60 therefore found nothing at all.
    """
    board = np.full((480, 640, 3), 162, np.uint8)
    board[:, :, 0] = 168                                   # a faintly cool white, redness -6
    assert not laser_candidates(board, np.eye(3), 640, 480), "A bare board carries no dot"

    lit = board.copy()
    cv2.circle(lit, (300, 240), 4, (150, 150, 253), -1)
    cv2.circle(lit, (300, 240), 2, (170, 170, 255), -1)
    spots = laser_candidates(lit, np.eye(3), 640, 480)
    assert len(spots) == 1, f"The dot must be found exactly once, got {len(spots)}"
    assert math.dist(spots[0], (300, 240)) < 4

    # The weakest dot measured on the bench still has to register.
    faint = board.copy()
    cv2.circle(faint, (200, 300), 3, (168, 168, 197), -1)  # redness 29, the measured floor
    assert laser_candidates(faint, np.eye(3), 640, 480), "The faintest measured dot must register"
    assert not laser_candidates(faint, np.eye(3), 640, 480, redness_min=60), \
        "The old threshold is what missed it"

    # One dot arrives in pieces, and pieces a few pixels apart read as an unusable merge.
    split = board.copy()
    for at in ((400, 200), (406, 203), (398, 208)):
        cv2.circle(split, at, 2, (150, 150, 250), -1)
    assert len(laser_candidates(split, np.eye(3), 640, 480, merge=0)) > 1
    joined = laser_candidates(split, np.eye(3), 640, 480)
    assert len(joined) == 1, f"Pieces of one dot must join, got {len(joined)}"

    # Anything that merely looks warm is not a dot.
    warm = board.copy()
    cv2.rectangle(warm, (50, 50), (120, 120), (150, 158, 178), -1)   # redness 20
    assert not laser_candidates(warm, np.eye(3), 640, 480), "A warm patch is not a laser"


def check_identity():
    tracker = LaserTracker()
    assert tracker.update([(100, 100)], 1.0)[0] == {}, "Position alone cannot create identity"
    assert tracker.update([(100, 100)], 1.1, 1, 1.2)[0] == {}, "Ignore pre-settle frames"
    assert tracker.update([(100, 100), (200, 100)], 1.21, 1, 1.2)[0] == {}, "Ambiguous ID window"
    assert tracker.update([(100, 100)], 1.22, 1, 1.2)[0] == {1: (100, 100)}
    tracker.update([(300, 100)], 1.24, 2, 1.2)
    aims, confidence = tracker.update([(305, 100), (105, 100)], 1.30)
    assert aims == {1: (105, 100), 2: (305, 100)}, "Assignment is independent of component order"
    assert len(confidence) == 2
    assert tracker.update([(110, 100), (310, 100)], 1.31, None, 1.4)[0] == {}
    assert tracker.update([], 1.35)[0] == {}
    assert tracker.update([(105, 100), (305, 100)], 1.4)[0] == {}, "Lost identity needs a new ID window"
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


def check_blank_survey():
    width, height = 320, 180
    vision = Vision({"display": {"width": width, "height": height}})
    vision.matrix = np.eye(3)
    blank = np.full((height, width, 3), 200, np.uint8)
    ink = blank.copy()
    ink[75:85, 120:200] = 10
    noisy = ink.copy()
    noisy[100:110, 120:200] = 10
    old = np.ones((height, width), bool)
    vision.walls = vision.wall_filter.seed(old)
    with patch("beaver_battle.vision.monotonic", return_value=10.0):
        vision.begin_survey(0.3)
    generation = vision.calibration_generation
    reference = np.full(blank.shape, 250, np.float32)
    gain = np.array([200, 200, 200], np.float32)
    canvas = np.zeros(blank.shape, np.uint8)
    vision.board_gain = gain
    vision.process_walls(noisy, 10.2, vision.matrix, reference, gain, canvas, generation)
    assert vision.survey_samples == 0, "Projected menu frames must settle before scanning"
    vision.process_walls(noisy, 10.5, vision.matrix, reference, gain, canvas, generation - 1)
    assert vision.survey_samples == 0, "Old in-flight work cannot enter a new survey"
    for frame, timestamp in ((noisy, 10.5), (ink, 10.6), (ink, 10.81)):
        vision.process_walls(frame, timestamp, vision.matrix, reference, gain, canvas, generation)
    assert not vision.surveying()
    assert vision.walls[80, 150] and not vision.walls[105, 150], "Majority keeps ink and rejects transient noise"
    assert vision.latest.walls is vision.walls, "Completed walls publish before survey completion is exposed"
    assert vision.board_gain is gain and vision.board_reference is not None
    assert vision.walls.mean() < 0.05, "Stale menu compensation must not enter the blank-board scan"


def check_fast_tracking():
    # Keep two identities well separated vertically while they sweep and reverse.
    def seeded_tracker(**settings):
        tracker = LaserTracker(**settings)
        tracker.update([(100, 100)], 1.0, 1)
        tracker.update([(100, 500)], 1.0, 2)
        return tracker

    points = [(200, 100), (200, 500)]
    assert seeded_tracker().update(points, 1 + 1 / 30)[0] == {}, "Legacy speed gate rejects a 100px/frame jump"
    tracker = seeded_tracker(max_speed=3000)
    # Two stationary observations let the velocity estimate settle at the turn.
    positions = (200, 300, 400, 500, 500, 500, 400, 300, 200, 100)
    for index, x in enumerate(positions, 1):
        aims = tracker.update([(x, 500), (x, 100)], 1 + index / 30)[0]
        assert aims == {1: (x, 100), 2: (x, 500)}, (index, aims)
    limited = seeded_tracker(max_speed=3000, max_gate=80)
    assert limited.update(points, 1 + 1 / 30)[0] == {}, "The configured absolute gate still caps motion"

    crossing = LaserTracker(max_speed=3000)
    crossing.update([(100, 100)], 2.0, 1)
    crossing.update([(300, 100)], 2.0, 2)
    assert crossing.update([(200, 100)], 2 + 1 / 30)[0] == {}, "Faster gates must still reject merged dots"
    assert crossing.update([(100, 100), (300, 100)], 2 + 2 / 30)[0] == {}, "Separation must not invent identities after crossing"

    abrupt = seeded_tracker(max_speed=3000)
    abrupt.update(points, 1 + 1 / 30)
    assert abrupt.update([(100, 100), (100, 500)], 1 + 2 / 30)[0] == {1: (100, 100), 2: (100, 500)}, \
        "An instantaneous reversal within the displacement gate retains identities"
    for index, x in enumerate((200, 100, 200, 100, 200, 100), 3):
        assert abrupt.update([(x, 500), (x, 100)], 1 + index / 30)[0] == {1: (x, 100), 2: (x, 500)}, \
            "Repeated sharp turns must not require stationary frames to reacquire"
    assert abrupt.update([(600, 100), (600, 500)], 1 + 9 / 30)[0] == {}, \
        "A physically implausible jump still invalidates identities"

    for settings in ({}, {"laser_max_speed": 3000, "laser_max_gate": 150}):
        vision = Vision({"camera": settings})
        with patch.object(vision, "capture_loop"), patch.object(vision, "process_loop"):
            vision.start()
            vision.stop()
        assert vision.tracker.max_speed == settings.get("laser_max_speed", 1200)
        assert vision.tracker.max_gate == settings.get("laser_max_gate", 180)


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
    assert walls.update(ink) is solid, "Unchanged geometry must reuse its immutable snapshot"
    assert np.array_equal(walls.update(empty), ink), "One bright frame cannot erase physical ink"
    walls.update(empty)
    assert not walls.update(empty).any(), "Erasure must update live geometry"
    assert np.array_equal(solid, ink), "Published wall arrays must not mutate later"


def check_early_aims():
    vision = Vision({"display": {"width": 320, "height": 240}, "camera": {"wall_persistence": 1}})
    vision.wall_filter = WallFilter(1)
    frame = np.full((240, 320, 3), 220, np.uint8)
    cv2.circle(frame, (100, 100), 3, (0, 0, 255), -1)
    frame[150:180, 180:200] = 0
    vision.camera_shape = frame.shape[:2]
    vision.matrix = np.eye(3)
    vision.set_identity(1, 0)
    scanning, release, completed = Event(), Event(), Event()
    original_ink = ink_mask

    def slow_walls(*args):
        scanning.set()
        assert release.wait(2)
        return original_ink(*args)

    process = vision.process_frame

    def observed_process(*args):
        result = process(*args)
        completed.set()
        return result

    vision.process_frame = observed_process
    with patch("beaver_battle.vision.ink_mask", side_effect=slow_walls):
        vision.process_thread = Thread(target=vision.process_loop)
        vision.process_thread.start()
        try:
            stamp = monotonic()
            with vision.lock:
                vision.frame = (stamp, frame)
            vision.frame_ready.set()
            assert scanning.wait(1)
            assert vision.latest.timestamp == stamp and 1 in vision.latest.aims, \
                "Fresh aim must publish before wall processing completes"
            assert vision.latest.walls is None
            vision.report_error("test capture disconnect", camera_failure=True)
            release.set()
            assert completed.wait(1)
            await_condition(lambda: vision.latest.walls is not None)
            assert vision.latest.walls[160, 190], "Wall extraction still completes"
            assert not vision.latest.aims and "disconnect" in vision.latest.error, \
                "An in-flight completion cannot undo a capture error"
        finally:
            release.set()
            vision.stop()

    vision.stopping.clear()
    previous = vision.latest
    snapshot = process(frame, monotonic(), 1, 0)
    assert vision.latest is previous, "Synchronous processing does not publish"
    generation = vision.calibration_generation
    vision.begin_calibration()
    previous = vision.latest
    vision.publish_snapshot(snapshot, generation)
    assert vision.latest is previous, "A superseded generation cannot publish early aims"
    vision.capture_error = ""
    vision.publish_snapshot(snapshot, vision.calibration_generation)
    assert not vision.latest.aims and not vision.latest.calibrated, "Calibration suppresses publication of aims"


def check_wall_worker():
    for cancel in (False, True):
        vision = Vision({"display": {"width": 320, "height": 240},
                         "camera": {"wall_persistence": 1, "wall_update_hz": 30}})
        vision.wall_filter = WallFilter(1)
        frame = np.full((240, 320, 3), 220, np.uint8)
        cv2.circle(frame, (100, 100), 3, (0, 0, 255), -1)
        frame[150:180, 180:200] = 0
        vision.camera_shape = frame.shape[:2]
        vision.matrix = np.eye(3)
        vision.set_identity(1, 0)
        scanning, release = Event(), Event()
        jobs = []
        process = vision.process_walls

        def slow_walls(*args):
            jobs.append(args[1])
            if len(jobs) == 1:
                scanning.set()
                assert release.wait(2)
            return process(*args)

        vision.process_walls = slow_walls
        vision.wall_thread = Thread(target=vision.wall_loop)
        vision.process_thread = Thread(target=vision.process_loop)
        vision.wall_thread.start()
        vision.process_thread.start()
        try:
            stamp = monotonic()
            for index in range(5):
                moment = stamp + index * 0.05
                with vision.lock:
                    vision.frame = (moment, frame)
                vision.frame_ready.set()
                await_condition(lambda: vision.latest.timestamp == moment)
                assert 1 in vision.latest.aims, "New frames publish aims while a wall scan is blocked"
                if index == 0:
                    assert scanning.wait(1)
            assert jobs == [stamp], "Wall work is single-owner with one latest pending slot"
            if cancel:
                vision.begin_calibration()
            release.set()
            await_condition(lambda: len(jobs) == 2)
            if cancel:
                assert vision.walls is None, "An old generation cannot commit walls after calibration begins"
                assert not vision.latest.calibrated and not vision.latest.aims
            else:
                await_condition(lambda: vision.latest.walls is not None)
                assert vision.latest.timestamp == stamp + 0.2, "Wall completion cannot roll back the newest aim frame"
                assert jobs == [stamp, stamp + 0.2], "Skipped wall frames never create backlog"
                assert vision.latest.walls[160, 190]
        finally:
            release.set()
            vision.stop()
        assert not vision.wall_thread.is_alive() and not vision.process_thread.is_alive()


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
        assert calibrated.calibrated
        # Calibration reads the board immediately, and its own markers are not the board.
        assert calibrated.walls is not None and not calibrated.walls.any()
        assert not calibrated.walls.flags.writeable
        frame = blank.copy()
        frame[150:220, 350:365] = 15
        frame[50:100, 100:200] = (255, 80, 20)
        cv2.circle(frame, (280, 100), 3, (0, 0, 255), -1)
        identified = vision.process_frame(frame, 1.7, 1, 1.65)
        assert np.linalg.norm(np.asarray(identified.aims[1]) - (280, 100)) < 2
        # Ink is read from red, so blue artwork is only separable from blue ink once the
        # worker is told what it projected. Hand over the frame that produced this view.
        canvas = np.full((height, width, 3), 255, np.uint8)
        canvas[50:100, 100:200] = (20, 80, 255)
        vision.set_projection(canvas, 1.85)
        second = vision.process_frame(frame, 1.85)
        assert second.walls[180, 357], "Physical dark line becomes wall"
        assert not second.walls[70, 150], "Projected art is undone before ink is read"
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


def check_cancel_calibration():
    width, height = 320, 240
    for previously_calibrated in (False, True):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "calibration.npz"
            config = {"display": {"width": width, "height": height},
                      "camera": {"calibration_file": str(path), "wall_persistence": 1}}
            original = np.array([[1., 0., 3.], [0., 1., 2.], [0., 0., 1.]])
            if previously_calibrated:
                save_calibration(path, original, (height, width), (height, width))
            vision = Vision(config)
            blank = np.full((height, width, 3), 220, np.uint8)
            blank[160:175, 120:140] = 15
            vision.latest = vision.process_frame(blank, monotonic())
            old_walls = vision.walls
            saved_bytes = path.read_bytes() if path.exists() else None
            detecting, release, completed = Event(), Event(), Event()

            def delayed_detection(found, screen_width, screen_height, **options):
                detecting.set()
                assert release.wait(2), "Cancellation must not wait for marker detection"
                return np.eye(3)

            process = vision.process_frame

            def observed_process(frame, timestamp, identity, ready_since):
                result = process(frame, timestamp, identity, ready_since)
                completed.set()
                return result

            vision.process_frame = observed_process
            vision.begin_calibration()
            assert not vision.snapshot().calibrated
            with patch("beaver_battle.vision.homography", side_effect=delayed_detection):
                vision.process_thread = Thread(target=vision.process_loop)
                vision.process_thread.start()
                try:
                    with vision.lock:
                        vision.frame = (monotonic(), calibration_image(width, height))
                    vision.frame_ready.set()
                    assert detecting.wait(1)
                    cancelled_at = monotonic()
                    vision.cancel_calibration()
                    vision.cancel_calibration()
                    assert not vision.calibration_requested.is_set()
                    assert vision.snapshot().calibrated == previously_calibrated
                    assert not vision.snapshot().aims
                    release.set()
                    assert completed.wait(1)
                finally:
                    release.set()
                    vision.stop()
            assert vision.latest.calibrated == previously_calibrated
            assert vision.walls is old_walls
            if previously_calibrated:
                assert np.array_equal(vision.matrix, original), "Cancelled result replaced the previous mapping"
                assert path.read_bytes() == saved_bytes, "Cancelled result overwrote saved calibration"
                vision.process_frame = process
                assert process(calibration_image(width, height), cancelled_at + 0.25).walls is old_walls
                fresh = process(blank, cancelled_at + 0.7)
                assert fresh.calibrated and vision.last_wall_time == cancelled_at + 0.7, \
                    "Wall learning resumes after marker settling; unchanged walls reuse the snapshot"
            else:
                assert vision.matrix is None and not path.exists(), "Cancelled first calibration must remain unavailable"
                assert "Calibration required" in vision.latest.error

            def superseded_detection(found, screen_width, screen_height, **options):
                vision.cancel_calibration()
                vision.begin_calibration()
                return np.eye(3)

            vision.process_frame = process
            vision.begin_calibration()
            with patch("beaver_battle.vision.homography", side_effect=superseded_detection):
                snapshot = process(blank, monotonic())
            assert vision.calibration_requested.is_set(), "Obsolete detection cleared a newer calibration request"
            assert not snapshot.calibrated


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
        assert backend == capture_backend(), "Capture must request this host's backend"
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
check_ink()
check_ink_colours()
check_scan()
check_obstacles()
check_projection_handover()
check_marker_memory()
check_backend()
check_candidates()
check_laser_on_a_whiteboard()
check_identity()
check_fast_tracking()
check_blank_survey()
check_walls()
check_early_aims()
check_wall_worker()
check_pipeline()
check_cancel_calibration()
check_worker()
print("Vision checks passed: calibration/cancellation/guidance, laser dot on a whiteboard, drawn ink by pen colour, blank-board survey, board scan, live obstacles, multi-frame markers, host capture backend, laser identity/overlap, "
      "walls, immutable/stale snapshots, capture backlog/reconnect/release")
