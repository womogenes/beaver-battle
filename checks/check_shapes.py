"""Run from the repository root: .venv/bin/python -m checks.check_shapes.

Enclosure filling decides whether someone's homework becomes a playable map, so the
shapes a whiteboard actually carries are checked one by one rather than by eye.
"""

import cv2
import numpy as np

from beaver_battle.game import closed_shapes, link_strokes
from checks.board_shapes import CASES, HEIGHT, WIDTH, arc, blank

MIN_AREA = 1200
MAX_AREA = .25 * WIDTH * HEIGHT
CLOSURE = .20


def check_cases():
    failures = []
    for name, build, want, why in CASES:
        walls = build().astype(bool)
        filled, shapes = closed_shapes(walls, 5, MIN_AREA, MAX_AREA, CLOSURE)
        if len(shapes) != want:
            failures.append(f"{name}: filled {len(shapes)} bodies, wanted {want} ({why})")
        assert filled[walls].all(), f"{name}: filling must never erase ink"
        if want == 0:
            assert int(filled.sum()) - int(walls.sum()) < MIN_AREA, \
                f"{name}: nothing should have been flooded ({why})"
    assert not failures, "\n  ".join([""] + failures)


def check_gap_is_judged_against_size():
    """The same gap must close a small ring and leave a large one open."""
    small = arc(blank(), (300, 300), 40, 0, 360, gaps=[(40, 8)]).astype(bool)
    large = arc(blank(), (300, 360), 250, 0, 360, gaps=[(40, 8)]).astype(bool)
    assert len(closed_shapes(small, 5, MIN_AREA, MAX_AREA, CLOSURE)[1]) == 1
    assert len(closed_shapes(large, 5, MIN_AREA, MAX_AREA, CLOSURE)[1]) == 1, \
        "A gap this small is negligible on either ring"
    # The same gap in absolute pixels, which one ring can carry and the other cannot.
    small_wide = arc(blank(), (300, 300), 40, 0, 360, gaps=[(40, 40)]).astype(bool)
    large_same = arc(blank(), (300, 360), 250, 0, 360, gaps=[(40, 40)]).astype(bool)
    assert len(closed_shapes(small_wide, 5, MIN_AREA, MAX_AREA, CLOSURE)[1]) == 0, \
        "Half a small ring missing is not a container"
    assert len(closed_shapes(large_same, 5, MIN_AREA, MAX_AREA, CLOSURE)[1]) == 1, \
        "The same absolute gap is still negligible on a big ring"


def check_filled_body_is_solid():
    """A filled enclosure must be solid through the middle, not just at its outline."""
    walls = CASES[0][1]().astype(bool)
    filled, shapes = closed_shapes(walls, 5, MIN_AREA, MAX_AREA, CLOSURE)
    assert len(shapes) == 1
    assert filled[300, 300], "The centre of a filled circle must be solid"
    assert int(filled.sum()) > 20000, "A 180 pixel circle should fill to roughly its area"
    assert not filled[50, 50], "Open water stays open"


def check_edge_and_size_limits():
    board = blank()
    cv2.rectangle(board, (-40, 300), (400, 500), 1, 3)
    filled, shapes = closed_shapes(board.astype(bool), 5, MIN_AREA, MAX_AREA, CLOSURE)
    assert len(shapes) <= 1, "A box running off the board must not flood the board"
    assert int(filled.sum()) < MAX_AREA


def pieces(mask):
    count, _ = cv2.connectedComponents(np.asarray(mask).astype(np.uint8), connectivity=8)
    return count - 1


def closest_separation(mask):
    """The narrowest gap between a mask's own pieces: the widest way through a barrier."""
    mask = np.asarray(mask).astype(np.uint8)
    count, labels, _, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 2:
        return 0.0
    spread = []
    for index in range(1, count):
        here = np.column_stack(np.nonzero(labels == index))
        for other in range(index + 1, count):
            there = np.column_stack(np.nonzero(labels == other))
            spread.append(float(np.linalg.norm(here[:, None] - there[None], axis=2).min()))
    return min(spread)


def check_stroke_linking():
    """A line drawn in one movement must hold as one barrier however the camera broke it."""
    whole = blank()
    points = np.array([[520, 120], [700, 200], [760, 340], [660, 470], [560, 560], [540, 660]])
    cv2.polylines(whole, [points.reshape(-1, 1, 2)], False, 1, 3)
    broken = whole.copy()
    for low, high in ((205, 250), (400, 448), (520, 556)):
        broken[low:high] = 0
    assert pieces(broken) == 4, f"The cuts must really break the stroke, got {pieces(broken)}"
    assert closest_separation(broken) > 36, "The breaks must be wide enough for a canoe"

    linked = link_strokes(broken.astype(bool), 70)
    assert pieces(linked) == 1, f"A broken stroke must rejoin, got {pieces(linked)} pieces"

    assert pieces(link_strokes(whole.astype(bool), 70)) == 1
    assert int(link_strokes(whole.astype(bool), 70).sum()) - int(whole.sum()) < 40, \
        "A stroke that is already whole must not be thickened"

    # Separate drawings stay separate, however tidily they sit beside each other.
    apart = blank()
    cv2.circle(apart, (250, 250), 60, 1, 3)
    cv2.circle(apart, (250, 600), 60, 1, 3)
    cv2.rectangle(apart, (800, 200), (1000, 400), 1, 3)
    assert pieces(link_strokes(apart.astype(bool), 70)) == pieces(apart), \
        "Drawings further apart than the reach must not be joined"

    # Specks are not strokes and must not sprout bridges to whatever is near them.
    speckled = whole.copy()
    for at in ((300, 300), (330, 320), (360, 300)):
        cv2.circle(speckled, at, 2, 1, -1)
    joined = link_strokes(speckled.astype(bool), 70, min_piece=40)
    assert int(joined.sum()) - int(speckled.sum()) < 40, "Specks must not be linked in"


def check_cost():
    from time import perf_counter
    walls = CASES[6][1]().astype(bool)
    closed_shapes(walls, 5, MIN_AREA, MAX_AREA, CLOSURE)
    start = perf_counter()
    for _ in range(10):
        closed_shapes(walls, 5, MIN_AREA, MAX_AREA, CLOSURE)
    spent = (perf_counter() - start) / 10
    assert spent < .040, f"Enclosure search must stay affordable at the wall rate, took {spent*1000:.0f} ms"


check_cases()
check_stroke_linking()
check_gap_is_judged_against_size()
check_filled_body_is_solid()
check_edge_and_size_limits()
check_cost()
print(f"Shape checks passed: {len(CASES)} whiteboard cases, broken strokes rejoined, "
      "gap judged against shape size, solid fills, edge and size limits, cost")
