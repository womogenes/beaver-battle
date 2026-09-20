"""Shapes a whiteboard actually carries, for exercising enclosure filling.

Homework is the target: equations, diagrams, axes, boxes and arrows, drawn by hand with
the gaps and wobble that implies. Each case names what filling should do with it and why,
so a change that quietly starts flooding an open curve is caught here rather than on the
board. Shared by checks.check_shapes and the tuning sweeps.
"""

import math

import cv2
import numpy as np

WIDTH, HEIGHT = 1280, 720


def blank():
    return np.zeros((HEIGHT, WIDTH), np.uint8)


def arc(image, centre, radius, start, end, thickness=3, gaps=()):
    """A ring segment carrying gaps of an exact width in pixels.

    Gaps are given in pixels rather than degrees because that is what the closing radius
    has to bridge, and what a reader can compare against the shape's own size.
    """
    blocked = []
    for at, pixels in gaps:
        half = math.degrees(pixels / (2.0 * max(radius, 1)))
        blocked.append((at - half, at + half))

    def open_at(angle):
        return not any(low <= angle <= high or low <= angle - 360 <= high
                       or low <= angle + 360 <= high for low, high in blocked)

    step = 0.5
    angle = start
    while angle < end:
        run_start = angle
        while angle < end and open_at(angle):
            angle += step
        if angle > run_start:
            cv2.ellipse(image, centre, (radius, radius), 0, run_start, angle, 1, thickness)
        while angle < end and not open_at(angle):
            angle += step
    return image


def circle_clean():
    return arc(blank(), (300, 300), 90, 0, 360)


def circle_pen_lift():
    """A 180-pixel circle with a 6-pixel gap where the pen lifted."""
    return arc(blank(), (300, 300), 90, 0, 360, gaps=[(40, 6)])


def circle_broken():
    """The same circle with a 26-pixel gap: wider than a canoe, small beside the shape."""
    return arc(blank(), (300, 300), 90, 0, 360, gaps=[(40, 26)])


def circle_dashed():
    """A dry marker: the same circle arriving as dashes with 14-pixel gaps."""
    return arc(blank(), (300, 300), 90, 0, 360,
               gaps=[(angle, 14) for angle in range(0, 360, 30)])


def circle_small_gapped():
    """A small circle with a gap in proportion to it. Scale must not decide the verdict."""
    return arc(blank(), (300, 300), 32, 0, 360, gaps=[(40, 9)])


def ring_bitten():
    """A small ring with a bite out of it, the size drawn on the bench in green.

    The gap is most of a radius, so nothing judging it in absolute pixels would call this
    closed. Against the ring's own size it is still plainly a container.
    """
    return arc(blank(), (300, 300), 34, 0, 360, thickness=6, gaps=[(40, 40)])


def ring_half_open():
    """Half the ring gone. Whatever the tolerance, this is not a container."""
    return arc(blank(), (300, 300), 90, 0, 180, thickness=4)


def letter_c():
    """Genuinely open: about 140 pixels of the ring missing, most of a side."""
    return arc(blank(), (300, 300), 90, 60, 330)


def swoosh():
    """The long open curve drawn on the bench, which must never flood."""
    image = blank()
    points = np.array([[520, 120], [700, 200], [760, 340], [660, 470], [560, 560], [540, 660]])
    cv2.polylines(image, [points.reshape(-1, 1, 2)], False, 1, 3)
    return image


def square():
    image = blank()
    cv2.rectangle(image, (200, 200), (420, 400), 1, 3)
    return image


def triangle():
    image = blank()
    cv2.polylines(image, [np.array([[300, 180], [430, 400], [170, 400]]).reshape(-1, 1, 2)], True, 1, 3)
    return image


def flowchart():
    """Two boxes joined by an arrow: both boxes fill, the arrow does not."""
    image = blank()
    cv2.rectangle(image, (150, 200), (350, 320), 1, 3)
    cv2.rectangle(image, (600, 200), (800, 320), 1, 3)
    cv2.line(image, (350, 260), (600, 260), 1, 3)
    cv2.line(image, (600, 260), (575, 245), 1, 3)
    cv2.line(image, (600, 260), (575, 275), 1, 3)
    return image


def venn():
    image = blank()
    arc(image, (400, 300), 110, 0, 360)
    arc(image, (560, 300), 110, 0, 360)
    return image


def nested():
    """A box inside a circle. Both are enclosures."""
    image = blank()
    arc(image, (400, 340), 170, 0, 360)
    cv2.rectangle(image, (330, 280), (470, 400), 1, 3)
    return image


def axes_and_curve():
    """A plot whose curve very nearly meets both axes.

    The area under the curve is a real enclosure and becoming an island is the point of
    playing on someone's homework, so this is expected to fill rather than to be refused.
    """
    image = blank()
    cv2.line(image, (200, 500), (800, 500), 1, 3)
    cv2.line(image, (200, 500), (200, 120), 1, 3)
    # A hill that comes back down to the axis at both ends, enclosing the area under it.
    xs = np.linspace(202, 798, 200)
    ys = 499 - 380 * (1 - ((xs - 500) / 298.0) ** 2)
    cv2.polylines(image, [np.column_stack([xs, ys]).astype(np.int32).reshape(-1, 1, 2)], False, 1, 3)
    return image


def plot_open():
    """The same plot with the axes running well past the curve. Plainly not a container."""
    image = blank()
    cv2.line(image, (150, 560), (1100, 560), 1, 3)
    cv2.line(image, (150, 560), (150, 90), 1, 3)
    xs = np.linspace(300, 700, 100)
    ys = 430 - ((xs - 500) / 400.0) ** 2 * 900
    cv2.polylines(image, [np.column_stack([xs, ys]).astype(np.int32).reshape(-1, 1, 2)], False, 1, 3)
    return image


def equation():
    """Handwriting at the size a whiteboard is actually written at.

    Counters inside letters are enclosures, so only the arena floor keeps them out.
    """
    image = blank()
    cv2.putText(image, "E = mc^2 + 40 sin(0)", (120, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 1, 3)
    cv2.putText(image, "dx/dt = 9.8 t + C", (120, 420), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 1, 3)
    return image


def spiral():
    image = blank()
    turns = np.linspace(0, 4 * np.pi, 400)
    radii = 12 + turns * 16
    points = np.column_stack([400 + radii * np.cos(turns), 340 + radii * np.sin(turns)])
    cv2.polylines(image, [points.astype(np.int32).reshape(-1, 1, 2)], False, 1, 3)
    return image


def arena_outline():
    """A border drawn round the whole board must not turn the board solid."""
    image = blank()
    cv2.rectangle(image, (60, 50), (WIDTH - 60, HEIGHT - 50), 1, 4)
    return image


def tiny_box():
    """Smaller than a canoe. Not worth being an arena."""
    image = blank()
    cv2.rectangle(image, (400, 300), (414, 314), 1, 2)
    return image


# name -> (builder, expected number of filled bodies, why)
CASES = [
    ("circle_clean", circle_clean, 1, "a closed outline is the basic case"),
    ("circle_pen_lift", circle_pen_lift, 1, "a few pixels of gap is still a circle"),
    ("circle_broken", circle_broken, 1, "a gap small against the shape should still fill"),
    ("circle_dashed", circle_dashed, 1, "a dry marker draws the same circle"),
    ("circle_small_gapped", circle_small_gapped, 1, "the same gap ratio on a smaller ring"),
    ("square", square, 1, "diagram boxes are the common case"),
    ("triangle", triangle, 1, "straight edges must work as well as curves"),
    ("flowchart", flowchart, 2, "both boxes fill, the joining arrow does not"),
    ("venn", venn, 3, "two overlapping rings enclose three regions"),
    ("nested", nested, 2, "a box inside a circle gives two arenas"),
    ("ring_bitten", ring_bitten, 1, "a bite out of a small ring still leaves a rock"),
    ("ring_half_open", ring_half_open, 0, "half a ring encloses nothing at any tolerance"),
    ("letter_c", letter_c, 0, "a quarter open is a C, not a container"),
    ("swoosh", swoosh, 0, "an open curve must never flood the board"),
    ("axes_and_curve", axes_and_curve, 1, "the area under a curve that meets its axes is an island"),
    ("plot_open", plot_open, 0, "axes running past the curve enclose nothing"),
    ("spiral", spiral, 0, "a spiral never closes on itself"),
    ("equation", equation, 0, "letter counters are far below the arena size"),
    ("arena_outline", arena_outline, 0, "a board border must leave the board playable"),
    ("tiny_box", tiny_box, 0, "smaller than a canoe is not an arena"),
]
