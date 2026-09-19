"""Run from the repository root: .venv/bin/python -m checks.check_shapes.

Enclosure filling decides whether someone's homework becomes a playable map, so the
shapes a whiteboard actually carries are checked one by one rather than by eye.
"""

import cv2
import numpy as np

from beaver_battle.game import closed_shapes
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
check_gap_is_judged_against_size()
check_filled_body_is_solid()
check_edge_and_size_limits()
check_cost()
print(f"Shape checks passed: {len(CASES)} whiteboard cases, gap judged against shape size, "
      "solid fills, edge and size limits, cost")
