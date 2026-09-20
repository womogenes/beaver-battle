"""Fixed-step canoe combat, independent of cameras and controller transport."""

from dataclasses import dataclass, field
import math
from pathlib import Path
import random

import cv2
import numpy as np
import pygame

from beaver_battle import sprites
from beaver_battle.juice import Juice
from beaver_battle.model import FeedbackEvent, PlayerInput


COLORS = sprites.PLAYER_COLORS
INK = sprites.INK
WATER = (255, 255, 255)


@dataclass
class Player:
    player_id: int
    pos: pygame.Vector2
    heading: float
    radius: float
    speed: float
    state: str = "canoe"
    ammo: int = 3
    reload: float = 0.0
    cooldown: float = 0.0
    invulnerability: float = 0.0
    powerup: str | None = None
    special_held: bool = False
    joust: float = 0.0
    bounce: float = 0.0
    wall: pygame.Vector2 = field(default_factory=pygame.Vector2)
    rescue: float = 0.0
    knock: pygame.Vector2 = field(default_factory=pygame.Vector2)


@dataclass
class Prop:
    kind: str
    pos: pygame.Vector2
    radius: float
    hp: int
    size: tuple[float, float] | None = None
    velocity: pygame.Vector2 = field(default_factory=pygame.Vector2)
    heading: float = 0.0
    cooldown: float = 0.8
    cycle: int = -1
    victims: set[int] = field(default_factory=set)
    beam_end: pygame.Vector2 | None = None


@dataclass
class Shape:
    kind: str
    contour: np.ndarray
    center: tuple[float, float]
    angle: float
    ratio: float


@dataclass
class Rock:
    owner: int
    pos: pygame.Vector2
    velocity: pygame.Vector2
    radius: float
    lifetime: float


@dataclass
class Pickup:
    kind: str
    pos: pygame.Vector2
    velocity: pygame.Vector2
    radius: float
    lifetime: float = 20.0
    charm: float = 0.0
    target: int | None = None


@dataclass
class Mine:
    owner: int
    pos: pygame.Vector2
    radius: float
    age: float = 0.0
    active: bool = True


@dataclass
class Effect:
    kind: str
    start: pygame.Vector2
    end: pygame.Vector2
    lifetime: float


def direction(angle):
    return pygame.Vector2(math.cos(angle), math.sin(angle))


def turn_toward(current, target, limit):
    difference = (target - current + math.pi) % math.tau - math.pi
    return current + max(-limit, min(limit, difference))


def countdown(value, dt):
    return 0.0 if value <= dt + 1e-9 else value - dt


def circle_hit(start, end, center, radius):
    delta = end - start
    offset = start - center
    distance = delta.length_squared()
    if offset.length_squared() <= radius * radius:
        return 0.0
    if distance == 0:
        return None
    middle = offset.dot(delta)
    discriminant = middle * middle - distance * (offset.length_squared() - radius * radius)
    if discriminant < 0:
        return None
    fraction = (-middle - math.sqrt(discriminant)) / distance
    return fraction if 0 <= fraction <= 1 else None


def prop_rect(prop):
    return pygame.FRect(prop.pos.x - prop.size[0] / 2, prop.pos.y - prop.size[1] / 2, *prop.size)


def stroke_ends(ink, look=15, min_branch=25):
    """Free ends of the drawing, with the direction each was travelling when it stopped.

    A break in a stroke leaves two ends facing one another. Two arms of one shape merely
    passing close by leave no ends at all, which is what separates a pen lift from a
    spiral's neighbouring turns and keeps this from welding a drawing shut.

    Thinning a hand-drawn blob sprouts short spurs all over it, and every spur looks like
    an end, so only ends belonging to a branch of at least `min_branch` pixels count. That
    is the difference between a line that stopped and a ragged edge.
    """
    # Thinning scans every pixel repeatedly. Empty water cannot contribute a stroke,
    # so retain only the ink and the neighbourhood used to measure its direction.
    left, top, width, height = cv2.boundingRect(ink)
    if not width or not height:
        return []
    pad = (max(3, int(look)) | 1) // 2 + 1
    right = min(ink.shape[1], left + width + pad)
    bottom = min(ink.shape[0], top + height + pad)
    left, top = max(0, left - pad), max(0, top - pad)
    ends = stroke_ends_patch(ink[top:bottom, left:right], look, min_branch)
    return [((x + left, y + top), heading) for (x, y), heading in ends]


def stroke_ends_patch(ink, look, min_branch):
    thin = cv2.ximgproc.thinning(ink * 255) > 0
    if not thin.any():
        return []
    # An integer kernel does not sum in filter2D; it has to be floating point.
    neighbours = cv2.filter2D(thin.astype(np.float32), cv2.CV_32F, np.ones((3, 3), np.float32),
                              borderType=cv2.BORDER_CONSTANT)
    tips = thin & (neighbours == 2)
    if not tips.any():
        return []
    # Cutting the junctions apart leaves plain arcs, whose pixel count is their length.
    arcs = thin & (neighbours <= 3)
    count, branch = cv2.connectedComponents(arcs.astype(np.uint8), connectivity=8)
    length = np.bincount(branch.ravel(), minlength=count)
    span = max(3, int(look)) | 1
    mass = cv2.blur(ink.astype(np.float32), (span, span))
    towards_x = cv2.blur((ink * np.arange(ink.shape[1])).astype(np.float32), (span, span))
    towards_y = cv2.blur((ink * np.arange(ink.shape[0])[:, None]).astype(np.float32), (span, span))
    ends = []
    for y, x in zip(*np.nonzero(tips)):
        piece = branch[y, x]
        if not piece or length[piece] < min_branch:
            continue
        weight = mass[y, x]
        if weight <= 0:
            continue
        # The stroke's body lies behind the end, so heading away from it is heading on.
        away = np.array([x - towards_x[y, x] / weight, y - towards_y[y, x] / weight])
        reach = np.hypot(*away)
        if reach < 1.0:
            continue
        ends.append(((int(x), int(y)), away / reach))
    return ends


def mend_breaks(walls, reach=90, spread=60, thickness=3, look=15, min_branch=25, record=None):
    """Carry a stroke's free end on to whatever it was heading for. Returns the mended mask.

    link_strokes joins separate pieces, which leaves the case that broke the bench board:
    a stroke interrupted part way round a loop, whose two sides are still one piece
    because they meet somewhere else entirely. Connectivity cannot see that gap, and a
    canoe fits through it.

    Each free end is carried forward within `spread` degrees of the way it was going and
    joined to the first ink within `reach`. Requiring a free end, and requiring the ink to
    lie ahead of the stroke rather than beside it, is what keeps a spiral or a letter C
    from being welded shut: their arms come close, but neither ends pointing at the other.
    """
    ink = walls.astype(np.uint8)
    ends = stroke_ends(ink, look, min_branch)
    if not ends:
        return walls
    steps = np.arange(look, int(reach) + 1)
    angles = np.radians(np.arange(-int(spread), int(spread) + 1, 5))
    mended = ink.copy()
    height, width = ink.shape
    for (x, y), heading in ends:
        base = math.atan2(heading[1], heading[0]) + angles
        xs = np.clip(np.rint(x + np.cos(base)[:, None] * steps).astype(int), 0, width - 1)
        ys = np.clip(np.rint(y + np.sin(base)[:, None] * steps).astype(int), 0, height - 1)
        hits = ink[ys, xs] > 0
        if not hits.any():
            continue
        first = np.where(hits.any(axis=1), hits.argmax(axis=1), len(steps))
        ray = int(np.argmin(first))
        if first[ray] >= len(steps):
            continue
        target = (int(xs[ray, first[ray]]), int(ys[ray, first[ray]]))
        cv2.line(mended, (x, y), target, 1, thickness)
        if record is not None:
            record.append(((x, y), target))
    return mended.astype(bool)


def link_strokes(walls, reach=70, min_piece=40, thickness=3, record=None):
    """Rejoin a stroke the camera broke into pieces. Returns the repaired mask.

    A line drawn in one movement arrives in fragments wherever the pen ran dry or the ink
    went faint, and on the bench a single curve came through in seven pieces with 36 to 65
    pixel holes between them. A barrier with a hull-sized hole is not a barrier, and a
    player who drew one continuous line is entitled to one continuous wall, so fragments
    whose nearest points come within `reach` are joined by the shortest segment between
    them. A stroke that is already whole gains nothing and is left alone.

    Candidate pairs are found from one distance transform: where two background pixels
    side by side are closest to different pieces, the sum of their distances is the width
    of the channel separating those pieces. Only the pairs that pass then pay for an exact
    search, over contour points rather than every pixel.
    """
    ink = walls.astype(np.uint8)
    left, top, width, height = cv2.boundingRect(ink)
    if not width or not height:
        return walls
    # A candidate Voronoi boundary can sit outside the ink's bounds, but each side
    # must still lie within reach. Keep that margin (and the bridge width) without
    # distance-transforming the rest of the empty board.
    pad = max(1, math.ceil(reach) + 2, int(thickness) + 1)
    right, bottom = min(ink.shape[1], left + width + pad), min(ink.shape[0], top + height + pad)
    left, top = max(0, left - pad), max(0, top - pad)
    found = [] if record is not None else None
    patch = link_strokes_patch(ink[top:bottom, left:right], reach, min_piece, thickness, found)
    repaired = walls.copy()
    repaired[top:bottom, left:right] = patch
    if record is not None:
        record.extend(((x + left, y + top), (u + left, v + top))
                      for (x, y), (u, v) in found)
    return repaired


def link_strokes_patch(ink, reach, min_piece, thickness, record):
    count, pieces, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if count <= 2:
        return ink
    distance, nearest = cv2.distanceTransformWithLabels(
        1 - ink, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_CCOMP)
    owner = np.zeros(int(nearest.max()) + 1, np.int32)
    ys, xs = np.nonzero(ink)
    owner[nearest[ys, xs]] = pieces[ys, xs]
    narrowest = np.full(count * count, np.inf, np.float32)
    for sideways in (True, False):
        if sideways:
            left, right = owner[nearest[:, :-1]], owner[nearest[:, 1:]]
            width = distance[:, :-1] + distance[:, 1:]
        else:
            left, right = owner[nearest[:-1]], owner[nearest[1:]]
            width = distance[:-1] + distance[1:]
        split = (left != right) & (left > 0) & (right > 0) & (width <= reach)
        if not split.any():
            continue
        low = np.minimum(left[split], right[split]).astype(np.int64)
        high = np.maximum(left[split], right[split]).astype(np.int64)
        np.minimum.at(narrowest, low * count + high, width[split])
    channel = {(int(key // count), int(key % count)): float(narrowest[key])
               for key in np.nonzero(np.isfinite(narrowest))[0]}
    if not channel:
        return ink
    wanted = set(index for pair in channel for index in pair
                 if stats[index, cv2.CC_STAT_AREA] >= min_piece)
    outlines = {}
    found, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_TC89_L1)
    for contour in found:
        points = contour.reshape(-1, 2)
        piece = int(pieces[points[0][1], points[0][0]])
        if piece in wanted:
            outlines.setdefault(piece, []).append(points)
    # Every boundary pixel of a long thin stroke is thousands of points; a sparse walk
    # round it finds the same crossing place for a fraction of the pairwise work.
    edges = {}
    for piece, parts in outlines.items():
        points = np.vstack(parts)
        stride = max(1, len(points) // 300)
        edges[piece] = points[::stride]
    bridged = ink.copy()
    for (first, second) in sorted(channel, key=channel.get):
        if first not in edges or second not in edges:
            continue
        here, there = edges[first], edges[second]
        spans = np.linalg.norm(here[:, None, :] - there[None, :, :], axis=2)
        index = int(np.argmin(spans))
        if spans.flat[index] > reach:
            continue
        start = tuple(int(value) for value in here[index // spans.shape[1]])
        finish = tuple(int(value) for value in there[index % spans.shape[1]])
        cv2.line(bridged, start, finish, 1, thickness)
        if record is not None:
            record.append((start, finish))
    return bridged.astype(bool)


def closed_shapes(walls, gap=5, min_area=400, max_area=math.inf, closure=0.25):
    """Find regions enclosed by ink. Returns (ink plus interiors, shapes).

    A hand-drawn outline almost never closes, and a camera breaks it further wherever the
    pen ran dry, so demanding a watertight loop filled almost nothing of a real board.
    Ink is instead grown outward at increasing radii and an enclosure is taken at the
    first radius that reveals it, provided the bridged gap stays small beside the
    enclosure's own size: at most `closure` of its linear extent.

    That ratio is the whole judgement, and it is what separates a circle with a pen lift
    from a letter C. Both are rings with a gap; only one has a gap small compared to what
    it surrounds, and an absolute pixel tolerance cannot tell them apart because a large
    shape may be missing far more ink than a small one and still plainly be a container.

    Ink is grown rather than closed. A morphological closing joins two stroke ends when
    dilated, then severs them again when eroded, so it needs a radius several times the
    gap it is bridging and the ratio stops meaning anything. One distance transform of the
    background serves every radius, which is also what makes this affordable.

    Regions touching the board edge are open water, and enclosures above max_area stay
    hollow so an arena outline cannot turn the whole board solid.
    """
    ink = walls.astype(np.uint8)
    distance = cv2.distanceTransform((ink == 0).astype(np.uint8), cv2.DIST_L2, 3)
    interior = np.zeros_like(ink)
    accepted = np.zeros_like(ink)
    shapes = []
    # Beyond this no radius can satisfy the ratio, whatever it might enclose.
    reach = closure * math.sqrt(min(max_area, float(walls.size))) / 2
    radii, step = [0], max(1, int(gap) // 2)
    while step <= reach:
        radii.append(step)
        step *= 2
    for radius in radii:
        grown = (distance <= radius).astype(np.uint8)
        contours, hierarchy = cv2.findContours(grown, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        fresh = np.zeros_like(ink)
        for contour, links in zip(contours, hierarchy[0] if hierarchy is not None else []):
            if links[3] < 0:
                continue
            area = cv2.contourArea(contour)
            # The hole was measured after the ink grew inward over it, so restore that
            # before judging size. A small ring found at a large radius is otherwise
            # rejected for being small when most of what was measured is the growth.
            extent = math.sqrt(max(area, 0.0)) + 2 * radius
            if not min_area <= extent ** 2 <= max_area:
                continue
            if 2 * radius > closure * extent:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            x = min(max(int(moments["m10"] / moments["m00"]), 0), ink.shape[1] - 1)
            y = min(max(int(moments["m01"] / moments["m00"]), 0), ink.shape[0] - 1)
            if accepted[y, x]:
                continue
            outline = contour
            if radius:
                # The art must cover what is solid, so restore the growth on this outline
                # too, not only on the mask. Cropped to its own corner of the board.
                left, top, wide, tall = cv2.boundingRect(contour)
                pad = radius + 2
                patch = np.zeros((tall + 2 * pad, wide + 2 * pad), np.uint8)
                cv2.drawContours(patch, [contour], -1, 1, -1, offset=(pad - left, pad - top))
                span = 2 * radius + 1
                patch = cv2.dilate(patch, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (span, span)))
                grown, _ = cv2.findContours(patch, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if grown:
                    outline = max(grown, key=cv2.contourArea) + np.int32([left - pad, top - pad])
            center, (across, along), degrees = cv2.minAreaRect(outline)
            if across > along:
                across, along, degrees = along, across, degrees - 90
            ratio = along / max(across, 1)
            # minAreaRect's angle belongs to its first side; the grain follows the long side.
            shapes.append(Shape("log" if ratio >= 2 else "rock", outline, center,
                                math.radians(degrees + 90) % math.pi, ratio))
            cv2.drawContours(fresh, [outline], -1, 1, -1)
        # Only once a radius completes, so one pass can take concentric enclosures both.
        interior |= fresh
        accepted |= fresh
    return walls | interior.astype(bool), shapes


@dataclass
class Game:
    config: dict
    phase: str = "playing"
    scores: dict[int, int] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    props: list[Prop] = field(default_factory=list)
    rocks: list[Rock] = field(default_factory=list)
    pickups: list[Pickup] = field(default_factory=list)
    mines: list[Mine] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    events: list[FeedbackEvent] = field(default_factory=list)
    feedback_sequence: int = 0
    blocked: bool = False
    error: str = ""
    winner: int | None = None
    walls: np.ndarray | None = None
    wall_distance: np.ndarray | None = None
    wall_source: np.ndarray | None = None
    wall_masks: dict[int, np.ndarray] = field(default_factory=dict)
    sprites: dict[str, pygame.Surface] = field(default_factory=dict)
    fonts: dict[int, pygame.font.Font] = field(default_factory=dict)
    art: dict[tuple, pygame.Surface] = field(default_factory=dict)
    shore: float = 0.0
    shapes: list[Shape] = field(default_factory=list)
    shape_art: list | None = None
    sounds: list[str] = field(default_factory=list)
    names: dict = field(default_factory=dict)
    fx: list = field(default_factory=list)
    freeze: float = 0.0
    juice: Juice | None = None
    ink_art: object = None
    bridges: dict = field(default_factory=dict)
    wall_tick: int = 0
    sticks: list = field(default_factory=list)
    loose_ink: np.ndarray | None = None
    pads: list = field(default_factory=list)

    def setting(self, name, default):
        return self.config.get("game", {}).get(name, default)

    def new_match(self, player_ids, walls=None):
        ids = list(player_ids)
        if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids) or any(player not in (1, 2) for player in ids):
            raise ValueError("Choose one or two distinct controller IDs from 1, 2")
        self.width = int(self.setting("width", 1280))
        self.height = int(self.setting("height", 720))
        self.scale = min(self.width / 1280, self.height / 720)
        self.shore = 0
        self.art.clear()
        self.rng = random.Random(self.setting("seed", 2026))
        self.scores = dict.fromkeys(ids, 0)
        self.drop_bag = []
        self.wall_source = None
        self.bridges.clear()
        self.walls = None
        self.wall_distance = None
        self.wall_masks.clear()
        self.shapes = []
        self.shape_art = None
        cell = max(4, round(14 * self.scale))
        noise = np.random.default_rng(self.setting("seed", 2026)).random(
            (self.height // cell + 2, self.width // cell + 2)).astype(np.float32)
        # Board-anchored, so a rock's speckles hold still while its outline is redrawn.
        self.stone = cv2.resize(noise, (self.width, self.height), interpolation=cv2.INTER_LINEAR) > .6
        self.sprites.clear()
        directory = Path(self.setting("sprite_dir", "assets"))
        for kind in ("canoe", "beaver"):
            path = directory / f"{kind}.png"
            if path.is_file():
                self.sprites[kind] = pygame.image.load(path)
        self.replace_walls(walls)
        self.new_round()

    def new_round(self):
        self.phase = "playing"
        self.fx.append(("reset", None))
        self.time = 0.0
        self.round_timer = 0.0
        self.winner = None
        self.rocks.clear()
        self.pickups.clear()
        self.mines.clear()
        self.effects.clear()
        self.props.clear()
        self.players.clear()
        self.blocked = False
        self.error = ""
        layout = [
            ("asteroid", .28, .68, 22, 2, None),
            ("asteroid", .72, .28, 22, 2, None),
        ]
        self.pads = [pygame.Vector2(x * self.width, y * self.height) for x, y in
                     ((.23, .43), (.77, .57), (.50, .26), (.12, .66), (.88, .40), (.62, .86), (.36, .14), (.90, .84))]
        if self.setting("hazards", False):
            layout += [
                ("turret", .11, .65, 21, 3, None),
                ("turret", .89, .43, 21, 3, None),
                ("beam", .50, .10, 18, 3, None),
            ]
        for kind, x, y, radius, hp, size in layout:
            prop = Prop(kind, pygame.Vector2(x * self.width, y * self.height), radius * self.scale, hp,
                        tuple(value * self.scale for value in size) if size else None)
            if kind == "asteroid":
                prop.velocity = direction(self.rng.uniform(0, math.tau)) * 42 * self.scale
            if kind == "beam":
                prop.heading = math.pi / 2
            self.props.append(prop)
        positions = [(.13, .18), (.87, .18), (.50, .85)]
        for index, player_id in enumerate(self.scores):
            x, y = positions[index]
            pos = pygame.Vector2(x * self.width, y * self.height)
            delta = pygame.Vector2(self.width / 2, self.height / 2) - pos
            self.players[player_id] = Player(player_id, pos, math.atan2(delta.y, delta.x), 18 * self.scale,
                                             self.setting("canoe_speed", 240) * self.scale,
                                             ammo=self.setting("magazine", 3),
                                             invulnerability=self.setting("invulnerability", .8))
        self.resolve_walls()

    def replace_walls(self, walls):
        if walls is self.wall_source:
            return False
        if walls is not None and (walls.shape != (self.height, self.width) or walls.dtype != np.bool_):
            raise ValueError("Walls must be a bool array matching game height and width")
        self.wall_source = walls
        shapes = []
        ink = walls
        if walls is not None:
            # A line drawn in one movement must hold as one barrier, whatever the camera
            # made of it, so repair the stroke before anything else reads the geometry.
            thickness = max(1, round(self.setting("stroke_width", 3) * self.scale))
            found = []
            ink = link_strokes(walls, round(self.setting("stroke_link", 40) * self.scale),
                               round(self.setting("stroke_min_piece", 40) * self.scale ** 2),
                               thickness, record=found)
            # Then carry any free end on, which reaches breaks that connectivity cannot see.
            ink = mend_breaks(ink, round(self.setting("stroke_mend", 180) * self.scale),
                              self.setting("stroke_spread", 60), thickness, record=found)
            ink = self.hold_bridges(ink, found, thickness)
            walls, shapes = closed_shapes(ink, round(self.setting("shape_gap", 5) * self.scale),
                                          self.setting("shape_min_area", 1200) * self.scale ** 2,
                                          self.setting("shape_max_fraction", .25) * self.width * self.height,
                                          self.setting("shape_closure", .70))
        for shape in shapes:
            # Camera jitter must not flip a fill between log and rock or wobble its grain.
            for old in self.shapes:
                if math.dist(shape.center, old.center) < 24 * self.scale:
                    if 1.7 <= shape.ratio <= 2.3:
                        shape.kind = old.kind
                    if abs((shape.angle - old.angle + math.pi / 2) % math.pi - math.pi / 2) < math.radians(8):
                        shape.angle = old.angle
                    break
        self.shapes = shapes
        self.shape_art = None
        self.ink_art = None
        self.sticks, self.loose_ink = self.find_sticks(ink, shapes)
        self.walls = walls
        self.wall_distance = cv2.distanceTransform((~walls).astype(np.uint8), cv2.DIST_L2, 5) if walls is not None else None
        self.wall_masks.clear()
        return True

    def hold_bridges(self, ink, found, thickness):
        """Keep a repair in place for a while after the evidence for it flickers out.

        Whether a free end is visible on any one frame turns on a pixel or two, and a
        bridge appearing or vanishing takes a whole enclosure with it: measured on a still
        board, solid area swung by a factor of two and filled bodies came and went between
        six and ten. The board is not changing, only our reading of it, so a repair is
        remembered for `game.bridge_memory` wall updates and forgotten only once nothing
        has proposed it again for that long.
        """
        self.wall_tick += 1
        memory = max(1, int(self.setting("bridge_memory", 25)))
        for start, finish in found:
            # Round the ends so the same repair refreshes rather than piling up.
            key = (start[0] // 8, start[1] // 8, finish[0] // 8, finish[1] // 8)
            self.bridges[key] = (self.wall_tick, start, finish)
        held = ink.astype(np.uint8)
        for key, (seen, start, finish) in list(self.bridges.items()):
            if self.wall_tick - seen > memory:
                del self.bridges[key]
                continue
            cv2.line(held, start, finish, 1, thickness)
        return held.astype(bool)

    def find_sticks(self, ink, shapes):
        """Pick out long straight strokes for stick art. Every stroke is painted regardless.

        Stick art is decoration laid over the ink, not a substitute for it. A straight bar
        drawn across a curved fragment leaves the bends unpainted, and a component that is
        neither straight enough nor large enough used to be painted by nothing at all, so
        a beaver would stop dead against geometry the player could not see.
        """
        if ink is None:
            return [], None
        count, labels, stats, centers = cv2.connectedComponentsWithStats(ink.astype(np.uint8))
        outlines = set()
        for shape in shapes:
            points = shape.contour.reshape(-1, 2)
            # Restoring a dilated outline can extend beyond the camera canvas.
            # Only actual image pixels have component labels; negative indices
            # would otherwise silently sample the opposite edge.
            inside = ((points[:, 0] >= 0) & (points[:, 0] < labels.shape[1]) &
                      (points[:, 1] >= 0) & (points[:, 1] < labels.shape[0]))
            points = points[inside]
            outlines.update(int(label) for label in labels[points[:, 1], points[:, 0]] if label)
        sticks = []
        for label in range(1, count):
            if label in outlines or stats[label][4] < 40 * self.scale ** 2:
                continue
            ys, xs = np.nonzero(labels == label)
            center, (across, along), degrees = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
            if across > along:
                across, along, degrees = along, across, degrees - 90
            if across <= 18 * self.scale and along >= 3 * max(across, 1):
                sticks.append((center, along, max(across, 4 * self.scale), math.radians(degrees + 90)))
        return sticks, np.array(ink, dtype=bool)

    def wall_mask(self, radius):
        radius = max(0, math.ceil(radius))
        if radius not in self.wall_masks:
            if self.walls is None:
                mask = np.zeros((self.height, self.width), dtype=np.uint8)
            else:
                mask = (self.wall_distance <= radius).astype(np.uint8)
            self.wall_masks[radius] = mask
        return self.wall_masks[radius]

    def physical_free(self, pos, radius):
        edge = radius + self.shore
        if not edge <= pos.x < self.width - edge or not edge <= pos.y < self.height - edge:
            return False
        return not self.wall_mask(radius)[int(pos.y), int(pos.x)]

    def overlaps_prop(self, pos, radius, prop):
        if prop.size:
            rect = prop_rect(prop)
            nearest = pygame.Vector2(max(rect.left, min(pos.x, rect.right)),
                                     max(rect.top, min(pos.y, rect.bottom)))
            return pos.distance_squared_to(nearest) < radius * radius
        return pos.distance_squared_to(prop.pos) < (radius + prop.radius) ** 2

    def free(self, pos, radius, ignore=None):
        return self.physical_free(pos, radius) and all(
            prop is ignore or prop.hp <= 0 or not self.overlaps_prop(pos, radius, prop) for prop in self.props)

    def nearest_free(self, pos, radius, ignore=None):
        if self.free(pos, radius, ignore):
            return pos.copy()
        free = 1 - self.wall_mask(radius).copy()
        margin = max(1, math.ceil(radius + self.shore))
        free[:margin] = free[-margin:] = 0
        free[:, :margin] = free[:, -margin:] = 0
        for prop in self.props:
            if prop is ignore or prop.hp <= 0:
                continue
            if prop.size:
                rect = prop_rect(prop).inflate(2 * radius + 2, 2 * radius + 2)
                cv2.rectangle(free, (math.floor(rect.left), math.floor(rect.top)),
                              (math.ceil(rect.right), math.ceil(rect.bottom)), 0, -1)
            else:
                cv2.circle(free, (round(prop.pos.x), round(prop.pos.y)), math.ceil(prop.radius + radius + 1), 0, -1)
        ys, xs = np.nonzero(free)
        if not len(xs):
            return None
        nearest = np.argmin((xs - pos.x) ** 2 + (ys - pos.y) ** 2)
        return pygame.Vector2(int(xs[nearest]), int(ys[nearest]))

    def resolve_walls(self):
        self.blocked = False
        self.error = ""
        bodies = [prop for prop in self.props if prop.hp > 0]
        bodies += [player for player in self.players.values() if player.state != "eliminated"]
        bodies += self.pickups
        for body in bodies:
            position = self.nearest_free(body.pos, body.radius, body)
            if position is None:
                self.blocked = True
                self.error = "Clear space on the board"
                continue
            body.pos = position
        self.rocks = [rock for rock in self.rocks if self.physical_free(rock.pos, rock.radius)]
        self.mines = [mine for mine in self.mines if self.physical_free(mine.pos, mine.radius)]

    def move(self, body, velocity, dt):
        velocity = velocity.copy()
        count = max(1, math.ceil(velocity.length() * dt / max(body.radius / 2, 1)))
        for step in range(count):
            for axis in (0, 1):
                candidate = body.pos.copy()
                candidate[axis] += velocity[axis] * dt / count
                if self.free(candidate, body.radius, body):
                    body.pos = candidate
                else:
                    velocity[axis] *= -1
        return velocity

    def hit_zone(self, target):
        """Where a shot counts, as circles: the whole hull of a canoe and the whole swim ring, as drawn.

        Movement still uses the single small circle, so boats slip past obstacles as easily as before.
        """
        if isinstance(target, Player) and target.state == "canoe":
            along = direction(target.heading) * target.radius * 1.35
            return [(target.pos + along * step, target.radius) for step in (-1, -.5, 0, .5, 1)]
        if isinstance(target, Player):
            return [(target.pos, target.radius * 1.9)]
        return [(target.pos, target.radius)]

    def trace(self, start, end, radius=0, owner=None, players=True, ignore=None):
        delta = end - start
        count = max(2, math.ceil(delta.length() / max(radius / 2, 1)) + 1)
        fractions = np.linspace(0, 1, count)
        xs = start.x + fractions * delta.x
        ys = start.y + fractions * delta.y
        edge = radius + self.shore
        outside = (xs < edge) | (ys < edge) | (xs >= self.width - edge) | (ys >= self.height - edge)
        occupied = self.wall_mask(radius)[np.clip(ys.astype(int), 0, self.height - 1),
                                          np.clip(xs.astype(int), 0, self.width - 1)]
        contacts = np.flatnonzero(outside | occupied.astype(bool))
        closest = float(fractions[contacts[0]]) if len(contacts) else 1.0
        hit = "wall" if len(contacts) else None
        targets = [prop for prop in self.props if prop.hp > 0 and prop is not ignore]
        if players:
            targets += [player for player in self.players.values()
                        if player.state != "eliminated" and player.player_id != owner]
            targets += [mine for mine in self.mines if mine.active]
        for target in targets:
            if isinstance(target, Prop) and target.size:
                clipped = prop_rect(target).inflate(2 * radius, 2 * radius).clipline(start, end)
                fraction = ((pygame.Vector2(clipped[0]) - start).dot(delta) / delta.length_squared()
                            if clipped and delta.length_squared() else None)
            else:
                touches = [circle_hit(start, end, center, radius + reach) for center, reach in self.hit_zone(target)]
                fraction = min((touch for touch in touches if touch is not None), default=None)
            if fraction is not None and 0 <= fraction <= closest:
                closest, hit = fraction, target
        return start + delta * closest, hit

    def hit(self, target, damage=1, push=None, by=None):
        push = pygame.Vector2(push).normalize() if push is not None and pygame.Vector2(push).length_squared() else pygame.Vector2()
        if isinstance(target, Player):
            if target.state == "eliminated" or target.invulnerability > 0:
                return False
            target.state = "beaver" if target.state == "canoe" else "eliminated"
            target.rescue = self.setting("canoe_return", 7)
            if target.state == "eliminated" and self.setting("scoring", "kills") == "kills" and by in self.scores and by != target.player_id:
                # Astro Party scoring: the point goes to whoever sank them, not to the last one afloat.
                self.scores[by] += 1
                self.fx.append(("score", target.pos.copy(), by))
            sunk = target.state == "eliminated"
            final = sunk and sum(player.state != "eliminated" for player in self.players.values()) <= 1
            self.sounds.append("splash" if sunk else "hit")
            if sunk:
                self.sounds.append("whoosh")
            # One hit should land like a truck: freeze the world, then throw the victim.
            self.freeze = max(self.freeze, self.setting("hit_stop", .09) * (1.7 if sunk else 1))
            target.knock = push * self.setting("knockback", 560) * self.scale
            self.fx.append(("sink" if sunk else "hit", target.pos.copy(), push, target.player_id, by, final))
            target.radius = 10 * self.scale
            target.speed = self.setting("beaver_speed", 75) * self.scale
            target.invulnerability = self.setting("invulnerability", .8)
            target.powerup = None
            target.joust = 0
            self.feedback_sequence += 1
            self.events.append(FeedbackEvent(target.player_id, self.feedback_sequence,
                                             duration_ms=self.config.get("feedback", {}).get("duration_ms", 200)))
            self.effects.append(Effect("splash", target.pos.copy(), pygame.Vector2(32 * self.scale, 0), .35))
            return True
        if isinstance(target, Prop) and target.hp > 0:
            target.hp -= damage
            self.sounds.append("thud" if target.hp > 0 else "crack")
            self.fx.append(("chip", target.pos.copy()))
            if target.hp <= 0 and target.kind in ("barrel", "asteroid"):
                if not self.drop_bag:
                    self.drop_bag = ["laser", "jouster", "mine"]
                    self.rng.shuffle(self.drop_bag)
                pickup = Pickup(self.drop_bag.pop(), target.pos.copy(),
                                direction(self.rng.uniform(0, math.tau)) * 35 * self.scale, 10 * self.scale)
                position = self.nearest_free(pickup.pos, pickup.radius)
                if position is not None:
                    pickup.pos = position
                    self.pickups.append(pickup)
            return True
        if isinstance(target, Mine) and target.active:
            self.explode(target)
            return True
        return False

    def shoot(self, owner, pos, heading, radius, ignore=None):
        forward = direction(heading)
        self.sounds.append("shoot")
        self.fx.append(("shoot", pos + forward * radius, owner, heading))
        start = pos + forward * (radius + 6 * self.scale)
        end, blocker = self.trace(pos, start, 4 * self.scale, owner=owner, ignore=ignore)
        if blocker is not None:
            self.hit(blocker, push=forward, by=owner)
        else:
            self.rocks.append(Rock(owner, start, forward * self.setting("rock_speed", 650) * self.scale,
                                   4 * self.scale, self.setting("rock_lifetime", 2)))

    def activate(self, player):
        kind = player.powerup
        if not kind or player.state != "canoe" or (player.reload > 0 and self.setting("reload_mode", "each") != "each"):
            return
        if kind == "laser":
            end, target = self.trace(player.pos, player.pos + direction(player.heading) * self.width * 2,
                                     2 * self.scale, owner=player.player_id)
            self.hit(target, 3, direction(player.heading), player.player_id)
            self.effects.append(Effect("laser", player.pos.copy(), end, .15))
            self.sounds.append("laser")
        elif kind == "jouster":
            player.joust = 2.0
            self.sounds.append("powerup")
        elif kind == "mine":
            pos = self.nearest_free(player.pos - direction(player.heading) * 32 * self.scale, 9 * self.scale)
            if pos is None:
                return
            self.mines.append(Mine(player.player_id, pos, 9 * self.scale))
            self.sounds.append("drop")
        player.powerup = None

    def explode(self, mine):
        mine.active = False
        self.sounds.append("boom")
        self.fx.append(("boom", mine.pos.copy()))
        radius = 90 * self.scale
        self.effects.append(Effect("explosion", mine.pos.copy(), pygame.Vector2(radius, 0), .3))
        targets = [player for player in self.players.values()
                   if player.state != "eliminated" and player.player_id != mine.owner]
        targets += [prop for prop in self.props if prop.hp > 0]
        # ponytail: tiny arenas scan every body; spatial indexing only pays off with hundreds of objects.
        for target in targets:
            if mine.pos.distance_to(target.pos) > radius + target.radius:
                continue
            end, blocker = self.trace(mine.pos, target.pos, players=False, ignore=target)
            if blocker is None:
                self.hit(target, 3, target.pos - mine.pos, mine.owner)

    def update(self, dt, inputs, walls=None):
        if not math.isfinite(dt) or not 0 <= dt <= 10:
            raise ValueError("Game dt must be finite and between zero and ten seconds")
        self.events = []
        del self.sounds[:-32]
        del self.fx[:-64]
        if walls is not None and self.replace_walls(walls):
            self.resolve_walls()
        if self.blocked or self.phase == "match_over":
            return self.events
        controls = inputs if isinstance(inputs, dict) else {item.player_id: item for item in inputs}
        count = max(1, math.ceil(dt * 60))
        for step in range(count):
            self.advance(dt / count, controls)
            if self.blocked or self.phase == "match_over":
                break
        return self.events

    def held(self, dt):
        """Hit-stop: after a hit the caller skips a few simulation steps so the blow can land.

        It lives outside update() so the simulation itself stays a pure function of its inputs.
        """
        if self.freeze <= 0:
            return False
        self.freeze = max(0.0, self.freeze - dt)
        return True

    def advance(self, dt, controls):
        if self.phase == "round_over":
            self.round_timer = countdown(self.round_timer, dt)
            if self.round_timer <= 0:
                self.new_round()
            return
        self.time += dt
        for effect in self.effects:
            effect.lifetime -= dt
        self.effects = [effect for effect in self.effects if effect.lifetime > 0]
        for player in self.players.values():
            if player.state == "eliminated":
                continue
            control = controls.get(player.player_id, PlayerInput(player.player_id, connected=False))
            player.invulnerability = countdown(player.invulnerability, dt)
            player.cooldown = countdown(player.cooldown, dt)
            player.joust = countdown(player.joust, dt)
            each = self.setting("reload_mode", "each") == "each"
            if player.reload > 0:
                player.reload = countdown(player.reload, dt)
                if player.reload == 0:
                    player.ammo = min(self.setting("magazine", 3), player.ammo + 1) if each else self.setting("magazine", 3)
                    if player.ammo < self.setting("magazine", 3):
                        player.reload = self.setting("reload_each", 1.0)
                    else:
                        self.sounds.append("reload")
            if player.state == "beaver" and self.setting("canoe_return", 7) > 0:
                # Survive long enough in the water and a fresh canoe arrives, with a moment's grace.
                player.rescue = countdown(player.rescue, dt)
                home = self.nearest_free(player.pos, 18 * self.scale, player) if player.rescue == 0 else None
                if home is not None:
                    player.state, player.pos, player.radius = "canoe", home, 18 * self.scale
                    player.ammo, player.reload, player.knock = self.setting("magazine", 3), 0.0, pygame.Vector2()
                    player.invulnerability = self.setting("return_invulnerability", 1.5)
                    self.sounds.append("return")
                    self.fx.append(("return", player.pos.copy(), player.player_id))
            player.bounce = countdown(player.bounce, dt)
            wish, rate = player.heading, math.radians(self.setting("turn_speed", 240))
            if control.connected and control.aim is not None and control.aim_age <= self.config.get("camera", {}).get("stale_seconds", .5):
                if all(math.isfinite(value) for value in control.aim):
                    delta = pygame.Vector2(control.aim) - player.pos
                    if delta.length() > self.setting("aim_deadzone", 24) * self.scale:
                        wish = math.atan2(delta.y, delta.x)
            if player.wall.length_squared():
                # Touching something: never steer into it. Hug it and slide along, instead of
                # rebounding, swinging back and hitting it again for as long as the aim is beyond it.
                want = direction(wish)
                along = pygame.Vector2(*(0 if want[axis] * player.wall[axis] > 0 else want[axis] for axis in (0, 1)))
                if along != want:
                    if along.length() < .2 and not (player.wall.x and player.wall.y):
                        # Dead ahead into one face: keep going whichever way the bow already favours.
                        ahead = direction(player.heading)
                        along = pygame.Vector2(*(0 if player.wall[axis] else ahead[axis] for axis in (0, 1)))
                        if along.length() < .05:
                            along = pygame.Vector2(-player.wall.y, player.wall.x)
                    # Wedged in a corner with nowhere to slide: hold still rather than thrash.
                    wish, rate = (math.atan2(along.y, along.x), math.radians(720)) if along.length() >= .05 else (player.heading, 0)
            player.heading = turn_toward(player.heading, wish, rate * dt)
            fire = control.fire and control.connected
            special = control.special and control.connected
            trigger = control.connected and (control.special_pressed or (special and not player.special_held))
            control.special_pressed = False  # Consume a batched tap once across fixed physics steps.
            player.special_held = special
            had_powerup = bool(player.powerup)
            if trigger and had_powerup:
                self.activate(player)
            if player.state == "beaver":
                target_speed = self.setting("beaver_boost_speed", 150) if fire else self.setting("beaver_speed", 75)
                change = 300 * self.scale * dt
                player.speed += max(-change, min(change, target_speed * self.scale - player.speed))
            else:
                player.speed = self.setting("canoe_speed", 240) * self.scale
            velocity = direction(player.heading) * player.speed * (1.5 if player.joust else 1)
            rebound = self.move(player, velocity, dt)
            blocked = pygame.Vector2(*(math.copysign(1, velocity[axis]) if rebound[axis] != velocity[axis] and velocity[axis] else 0 for axis in (0, 1)))
            # Only a real collision makes a noise, and not again while still leaning on the same edge.
            if blocked.length_squared() and player.bounce == 0 and not player.wall.length_squared() and direction(player.heading).dot(blocked.normalize()) > .5:
                self.sounds.append("bump")
                self.fx.append(("bump", player.pos + blocked.normalize() * player.radius))
            if blocked.length_squared():
                player.bounce = .5
            for axis in (0, 1):
                # Sliding flush along a face no longer pushes into it, but the face is still there.
                if not blocked[axis] and player.wall[axis]:
                    probe = player.pos.copy()
                    probe[axis] += player.wall[axis] * 2 * self.scale
                    if not self.free(probe, player.radius, player):
                        blocked[axis] = player.wall[axis]
            player.wall = blocked
            if player.knock.length_squared() > 4:
                player.knock = self.move(player, player.knock, dt) * .0009 ** dt
            if trigger and not had_powerup and player.state == "canoe" and player.cooldown == 0 and (player.ammo > 0 if each else player.reload == 0):
                self.shoot(player.player_id, player.pos, player.heading, player.radius)
                player.ammo -= 1
                player.cooldown = self.setting("shot_interval", .30)
                if each:
                    player.reload = player.reload or self.setting("reload_each", 1.0)
                elif player.ammo <= 0:
                    player.reload = self.setting("reload_seconds", 2.5)
            if player.state == "canoe" and self.setting("ram_swimmers", True):
                # A beaver in the water is fragile: a canoe only has to run it over.
                for other in self.players.values():
                    if other.state == "beaver" and other is not player and other.pos.distance_to(player.pos) <= player.radius + other.radius:
                        if self.hit(other, push=direction(player.heading), by=player.player_id):
                            self.sounds.append("ram")
            if player.joust:
                end, target = self.trace(player.pos, player.pos + direction(player.heading) * 54 * self.scale,
                                         7 * self.scale, owner=player.player_id)
                if target is not None and target != "wall":
                    self.sounds.append("ram")
                    self.hit(target, 3, direction(player.heading), player.player_id)
                    player.joust = 0
        for prop in self.props:
            if prop.hp > 0 and prop.velocity.length_squared():
                prop.velocity = self.move(prop, prop.velocity, dt)
        self.update_rocks(dt)
        self.update_hazards(dt)
        self.update_pickups(dt)
        self.update_mines(dt)
        self.settle()

    def settle(self):
        """Award the round as soon as one canoe is left, even in the middle of a hit-stop."""
        if self.phase != "playing":
            return
        survivors = [player.player_id for player in self.players.values() if player.state != "eliminated"]
        if len(self.players) > 1 and len(survivors) <= 1:
            self.winner = survivors[0] if survivors else None
            goal = self.setting("winning_score", 5)
            if self.setting("scoring", "kills") == "kills":
                # Points were scored as beavers sank. Reaching the goal ends the match at the end of a
                # round, unless the lead is shared: then play on until somebody is ahead (overtime).
                best = max(self.scores.values())
                leaders = [player_id for player_id, score in self.scores.items() if score == best]
                done = best >= goal and len(leaders) == 1
                if done:
                    self.winner = leaders[0]
            else:
                if self.winner is not None:
                    self.scores[self.winner] += 1
                done = self.winner is not None and self.scores[self.winner] >= goal
            self.phase = "match_over" if done else "round_over"
            self.sounds.append("fanfare" if self.phase == "match_over" else "win")
            self.fx.append(("round", None, self.winner, self.phase == "match_over"))
            self.round_timer = self.setting("round_delay", 3)

    def update_rocks(self, dt):
        remaining = []
        for rock in self.rocks:
            end, target = self.trace(rock.pos, rock.pos + rock.velocity * dt, rock.radius, owner=rock.owner)
            rock.pos = end
            rock.lifetime -= dt
            if target is not None:
                if not self.hit(target, push=rock.velocity, by=rock.owner):
                    self.fx.append(("chip", end.copy()))
            elif rock.lifetime > 0:
                remaining.append(rock)
        self.rocks = remaining

    def update_hazards(self, dt):
        for prop in self.props:
            if prop.hp <= 0:
                continue
            if prop.kind == "turret":
                prop.cooldown = countdown(prop.cooldown, dt)
                players = sorted((player for player in self.players.values() if player.state != "eliminated"),
                                 key=lambda player: player.pos.distance_squared_to(prop.pos))
                for player in players:
                    end, blocker = self.trace(prop.pos, player.pos, players=False, ignore=prop)
                    if blocker is not None:
                        continue
                    delta = player.pos - prop.pos
                    heading = math.atan2(delta.y, delta.x)
                    prop.heading = turn_toward(prop.heading, heading, math.radians(180) * dt)
                    if prop.cooldown == 0 and abs((heading - prop.heading + math.pi) % math.tau - math.pi) < .15:
                        self.shoot(0, prop.pos, prop.heading, prop.radius, ignore=prop)
                        prop.cooldown = 1.2
                    break
            elif prop.kind == "beam":
                cycle = int(self.time / 5)
                if prop.cycle != cycle:
                    prop.cycle = cycle
                    prop.victims.clear()
                stage = self.time % 5
                if stage < 1.5:
                    prop.beam_end, target = self.trace(prop.pos, prop.pos + direction(prop.heading) * self.width * 2,
                                                      2 * self.scale, players=False, ignore=prop)
                    if stage >= 1:
                        for player in self.players.values():
                            if player.player_id not in prop.victims and player.state != "eliminated":
                                if circle_hit(prop.pos, prop.beam_end, player.pos, player.radius + 4 * self.scale) is not None:
                                    self.hit(player)
                                    prop.victims.add(player.player_id)
                else:
                    prop.beam_end = None

    def update_pickups(self, dt):
        remaining = []
        for pickup in self.pickups:
            pickup.lifetime -= dt
            takers = [player for player in self.players.values() if player.state == "canoe" and player.powerup is None]
            taker = next((player for player in takers if player.player_id == pickup.target), None)
            if taker is None or taker.pos.distance_to(pickup.pos) > 200 * self.scale:
                # Nobody has it yet: lock on to the nearest canoe that strays close.
                pickup.target, pickup.charm = None, 0.0
                taker = min(takers, key=lambda player: player.pos.distance_squared_to(pickup.pos), default=None)
                if taker is not None and taker.pos.distance_to(pickup.pos) <= 125 * self.scale:
                    pickup.target = taker.player_id
                    self.sounds.append("charm")
                else:
                    taker = None
            if taker is None:
                pickup.velocity = self.move(pickup, pickup.velocity, dt)
            else:
                # Pulse on the spot for a beat, then zip to the canoe, faster the longer it flies.
                pickup.charm += dt
                gap = taker.pos - pickup.pos
                if pickup.charm > .22 and gap.length_squared():
                    pickup.pos += gap.normalize() * min(gap.length(), (520 + 3200 * (pickup.charm - .22)) * self.scale * dt)
            if taker is not None and taker.pos.distance_to(pickup.pos) <= taker.radius + pickup.radius:
                taker.powerup = pickup.kind
                self.sounds.append("pickup")
                self.fx.append(("pickup", taker.pos.copy(), taker.player_id, pickup.kind))
            elif pickup.lifetime > 0:
                remaining.append(pickup)
        self.pickups = remaining

    def update_mines(self, dt):
        for mine in self.mines:
            mine.age += dt
            if not mine.active or mine.age < .6:
                continue
            if mine.age > 15:
                mine.active = False
                continue
            for player in self.players.values():
                if player.player_id == mine.owner or player.state == "eliminated" or player.pos.distance_to(mine.pos) > 60 * self.scale:
                    continue
                end, blocker = self.trace(mine.pos, player.pos, players=False)
                if blocker is None:
                    self.explode(mine)
                    break
        self.mines = [mine for mine in self.mines if mine.active]

    def text(self, surface, message, position, size=20, color=None, centered=False, tilt=0):
        size = max(12, round(size * self.scale))
        image = sprites.label(str(message), size, color, tilt=tilt) if color else sprites.sign(str(message), size)
        surface.blit(image, image.get_rect(center=position) if centered else image.get_rect(topleft=position))

    def font(self, size):
        if size not in self.fonts:
            if not pygame.font.get_init():
                pygame.font.init()
            self.fonts[size] = pygame.font.Font(None, size)
        return self.fonts[size]

    def sprite(self, key, build):
        if key not in self.art:
            self.art[key] = build()
        return self.art[key]

    def stamp(self, surface, image, center, heading=None):
        if heading is not None:
            image = pygame.transform.rotozoom(image, -math.degrees(heading), 1)
        surface.blit(image, image.get_rect(center=center))

    def shape_image(self, shape):
        pad = math.ceil(6 * self.scale)
        x, y, width, height = cv2.boundingRect(shape.contour)
        mask = np.zeros((height + 2 * pad, width + 2 * pad), np.uint8)
        cv2.drawContours(mask, [shape.contour], -1, 255, -1, offset=(pad - x, pad - y))
        smooth = cv2.approxPolyDP(shape.contour, 1.5 * self.scale, True)
        edge = (smooth.reshape(-1, 2) + (pad - x, pad - y)).tolist()
        return sprites.outline_fill(shape.kind, cv2.GaussianBlur(mask, (3, 3), 0), shape.angle, edge, self.scale), (x - pad, y - pad)

    def stick_image(self, stick):
        center, along, across, angle = stick
        seed = round(center[0] / 40) * 97 + round(center[1] / 40)
        twig = self.sprite(("stick", round(along / 6), round(across / 2), seed),
                           lambda: sprites.stick(round(along / 6) * 6, max(4, round(across / 2) * 2), seed=seed))
        twig = pygame.transform.rotozoom(twig, -math.degrees(angle), 1)
        return twig, twig.get_rect(center=center).topleft

    def bob(self, index, topleft):
        """Floating things rock gently on the water; purely cosmetic."""
        return (topleft[0] + 1.6 * self.scale * math.sin(self.time * 1.9 + index * 2.1),
                topleft[1] + 1.6 * self.scale * math.cos(self.time * 1.4 + index * 1.3))

    def name(self, player_id):
        """What the players typed at the start, or PLAYER N."""
        return (self.names.get(player_id) or f"PLAYER {player_id}") if player_id else "DRAW"

    def portrait(self, player_id, zoom=1):
        """Drawn at the size it will be shown: enlarging a small sprite is what made the beavers blurry."""
        color = COLORS[player_id - 1]
        def build():
            ring, head = sprites.swimmer(10 * self.scale, color, zoom), sprites.tim(14.5 * self.scale, zoom, paws=False)
            image = pygame.Surface(ring.get_size(), pygame.SRCALPHA)
            image.blit(ring, (0, 0))
            image.blit(head, head.get_rect(center=image.get_rect().center))
            return image
        return self.sprite(("portrait", player_id, zoom), build)

    def draw(self, surface):
        surface.fill(WATER)
        if not self.players:
            return
        unit = self.scale
        if self.juice is None:
            self.juice = Juice(unit)
        juice = self.juice
        juice.step(self)
        # Compose off-screen in plain 24-bit RGB. A window's own surface can carry a transparency channel
        # (it does on macOS); once that holds zeros, text blitted onto it shows its invisible box.
        screen, surface = surface, self.sprite(("canvas",), lambda: pygame.Surface((self.width, self.height), 0, 24))
        surface.fill(WATER)
        sway = round(14 * unit)
        water = self.sprite(("water",), lambda: sprites.water(self.width + 2 * sway, self.height + 2 * sway, lighten=self.setting("water_wash", .8)))
        surface.blit(water, (-sway + sway * math.sin(self.time * .45), -sway + sway * math.cos(self.time * .35)))
        for index, pad in enumerate(self.pads):
            image = self.sprite(("pad", index), lambda: sprites.lily_pad(16 * unit, degrees=index * 67, flower=index % 3 != 2))
            surface.blit(image, self.bob(index + 20, image.get_rect(center=pad).topleft))
        if self.shape_art is None:
            self.shape_art = [self.shape_image(shape) for shape in self.shapes] + [self.stick_image(stick) for stick in self.sticks]
            self.ink_art = None
            if self.loose_ink is not None and self.loose_ink.any():
                # Everything solid is painted, at least as wide as it collides.
                width = max(1, round(self.setting("ink_width", 5) * self.scale)) | 1
                shown = cv2.dilate(self.loose_ink.astype(np.uint8),
                                   cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width, width))).astype(bool)
                ink = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                pixels, opacity = pygame.surfarray.pixels3d(ink), pygame.surfarray.pixels_alpha(ink)
                pixels[shown.T] = sprites.BARK_LINE
                opacity[shown.T] = 255
                del pixels, opacity
                self.ink_art = ink
        if self.ink_art is not None:
            # Never bobbed: ink is where the collision is, and must be drawn there.
            surface.blit(self.ink_art, (0, 0))
        surface.blits([(image, self.bob(index, topleft)) for index, (image, topleft) in enumerate(self.shape_art)])
        for index, prop in enumerate(self.props):
            if prop.hp <= 0:
                continue
            pos, radius = prop.pos, prop.radius
            if prop.size:
                self.stamp(surface, self.sprite(("log", index), lambda: sprites.log(prop.size[0] / unit, prop.size[1] / unit, unit, seed=index)), pos)
            elif prop.kind == "asteroid":
                self.stamp(surface, self.sprite(("boulder", index), lambda: sprites.boulder(radius, index)), pos)
            elif prop.kind == "barrel":
                self.stamp(surface, self.sprite(("barrel",), lambda: sprites.barrel(radius, self.font)), pos)
            else:
                self.stamp(surface, self.sprite(("pad", prop.kind), lambda: sprites.lily_pad(radius * 1.1, degrees=index * 70, flower=False)), pos)
                if prop.kind == "beam" and prop.beam_end is not None:
                    if self.time % 5 >= 1:
                        pygame.draw.line(surface, INK, pos, prop.beam_end, max(3, round(15 * unit)))
                        pygame.draw.line(surface, sprites.SKY_LIGHT, pos, prop.beam_end, max(2, round(10 * unit)))
                        pygame.draw.line(surface, sprites.WHITE, pos, prop.beam_end, max(1, round(4 * unit)))
                    else:
                        pygame.draw.line(surface, sprites.CREAM, pos, prop.beam_end, max(1, round(2 * unit)))
                head = sprites.beam_lotus if prop.kind == "beam" else sprites.squirt_plant
                self.stamp(surface, self.sprite(("head", prop.kind), lambda: head(radius)), pos, prop.heading)
            for pip in range(prop.hp):
                center = (pos.x + (pip - (prop.hp - 1) / 2) * 9 * unit, pos.y + radius + 10 * unit)
                pygame.draw.circle(surface, INK, center, max(2, round(4 * unit)))
                pygame.draw.circle(surface, sprites.CREAM, center, max(1, round(2.2 * unit)))
        juice.draw_under(surface)
        for pickup in self.pickups:
            bob = 1 + .06 * math.sin(self.time * 4 + pickup.pos.x)
            if pickup.target is not None:
                # Charmed: swell and throb before it leaps.
                bob = 1 + .45 * min(1, pickup.charm / .22) + .12 * math.sin(pickup.charm * 55)
            self.stamp(surface, self.sprite(("pickup", pickup.kind, round(bob, 2)), lambda: sprites.pickup(pickup.radius * bob, pickup.kind)), pickup.pos)
        for mine in self.mines:
            armed = mine.age >= .6
            self.stamp(surface, self.sprite(("mine", armed), lambda: sprites.pufferfish(mine.radius * 1.25, armed)), mine.pos)
        for rock in self.rocks:
            if rock.owner:
                self.stamp(surface, self.sprite(("pebble",), lambda: sprites.pebble(rock.radius * 1.4)), rock.pos, self.time * 9)
            else:
                self.stamp(surface, self.sprite(("droplet",), lambda: sprites.droplet(rock.radius * 1.4)), rock.pos,
                           math.atan2(rock.velocity.y, rock.velocity.x))
        for player in self.players.values():
            if player.state == "eliminated":
                continue
            color = COLORS[player.player_id - 1]
            forward = direction(player.heading)
            sprite = self.sprites.get(player.state)
            if sprite:
                image = pygame.transform.smoothscale(sprite, (max(1, round(player.radius * 3)), max(1, round(player.radius * 2))))
                self.stamp(surface, image, player.pos, player.heading)
            else:
                # Lean into turns, squash on recoil and impact, and spin away when hit.
                body = juice.body(player.player_id)
                lean, squash, spin = (body.lean, body.squash, body.spin) if body else (0, 0, 0)
                whirl = spin * spin * math.tau * 2
                side = direction(player.heading + math.pi / 2)
                if player.state == "canoe":
                    if player.joust:
                        self.stamp(surface, self.sprite(("horn",), lambda: sprites.horn(54 * unit)), player.pos + forward * (player.radius * 2.2 + 20 * unit), player.heading)
                    hull = self.sprite(("canoe", color, player.radius), lambda: sprites.canoe(player.radius, color))
                    head = self.sprite(("tim", player.radius), lambda: sprites.tim(player.radius * 1.05))
                else:
                    hull = self.sprite(("swimmer", color, player.radius), lambda: sprites.swimmer(player.radius, color))
                    head = self.sprite(("tim", player.radius, "swim"), lambda: sprites.tim(player.radius * 1.45, paws=False))
                if squash > .02 or abs(lean) > .05:
                    hull = pygame.transform.smoothscale(hull, (max(1, round(hull.get_width() * (1 - .13 * squash))),
                                                               max(1, round(hull.get_height() * (1 + .2 * squash - .1 * abs(lean))))))
                self.stamp(surface, hull, player.pos, player.heading + whirl)
                if squash > .02:
                    head = pygame.transform.smoothscale(head, (max(1, round(head.get_width() * (1 + .28 * squash))), max(1, round(head.get_height() * (1 - .22 * squash)))))
                if abs(lean) > .03 or spin > 0:
                    head = pygame.transform.rotozoom(head, -math.degrees(lean * .26 + whirl), 1)
                self.stamp(surface, head, player.pos - forward * 2 * unit + side * lean * 4 * unit)
            if player.invulnerability > 0:
                pygame.draw.circle(surface, sprites.CREAM, player.pos, player.radius * 2.5, max(1, round(3 * unit)))
            tag = player.pos + pygame.Vector2(0, -player.radius * 2 - 14 * unit)
            # A name plate over each boat: the first three letters of the name, or the player number.
            short = (self.names.get(player.player_id) or "").replace(" ", "")[:3] or str(player.player_id)
            word = sprites.lettering(short, max(12, round(19 * unit)), INK)
            plate = pygame.Rect(0, 0, word.get_width() + 16 * unit, 24 * unit)
            plate.center = tag
            pygame.draw.rect(surface, INK, plate.inflate(5 * unit, 5 * unit), border_radius=round(14 * unit))
            pygame.draw.rect(surface, sprites.tint(color, .45), plate, border_radius=round(12 * unit))
            surface.blit(word, word.get_rect(center=plate.center))
            if player.powerup:
                self.stamp(surface, self.sprite(("held", player.powerup), lambda: sprites.pickup(9 * unit, player.powerup)), (plate.right + 15 * unit, tag.y))
            if player.state == "canoe":
                for rock in range(self.setting("magazine", 3)):
                    center = player.pos + pygame.Vector2((rock - (self.setting("magazine", 3) - 1) / 2) * 10 * unit, player.radius * 2 + 8 * unit)
                    pygame.draw.circle(surface, INK, center, 4 * unit)
                    loaded = rock < player.ammo and (self.setting("reload_mode", "each") == "each" or not player.reload)
                    pygame.draw.circle(surface, sprites.STONE if loaded else sprites.WHITE, center, 2.6 * unit)
            elif player.rescue > 0 and self.setting("canoe_return", 7) > 0:
                # A ring that fills as the new canoe gets closer.
                ring = pygame.Rect(0, 0, player.radius * 5.2, player.radius * 5.2)
                ring.center = player.pos
                done = 1 - player.rescue / self.setting("canoe_return", 7)
                pygame.draw.arc(surface, INK, ring.inflate(4 * unit, 4 * unit), math.pi / 2 - done * math.tau, math.pi / 2, max(2, round(7 * unit)))
                pygame.draw.arc(surface, color, ring, math.pi / 2 - done * math.tau, math.pi / 2, max(1, round(4 * unit)))
        for effect in self.effects:
            if effect.kind == "laser":
                pygame.draw.line(surface, INK, effect.start, effect.end, max(3, round(12 * unit)))
                pygame.draw.line(surface, sprites.SKY_LIGHT, effect.start, effect.end, max(2, round(7 * unit)))
                pygame.draw.line(surface, sprites.WHITE, effect.start, effect.end, max(1, round(3 * unit)))
            else:
                radius = effect.end.x * (1 - effect.lifetime / (.35 if effect.kind == "splash" else .3))
                pygame.draw.circle(surface, sprites.WHITE if effect.kind == "splash" else sprites.BUTTER, effect.start,
                                   max(1, round(radius)), max(1, round(5 * unit)))
        juice.draw_over(surface)
        juice.draw_ghosts(surface, self.portrait)
        juice.draw_scores(surface)
        frame = self.sprite(("frame",), lambda: pygame.Surface((self.width, self.height), 0, 24))
        frame.fill(WATER)
        juice.present(surface, frame)
        surface = frame
        goal = self.setting("winning_score", 5)
        group = (46 + goal * 22) * unit
        left = self.width / 2 - (len(self.players) * group + (len(self.players) - 1) * 34 * unit) / 2
        for index, player in enumerate(self.players.values()):
            color = COLORS[player.player_id - 1]
            x, y = left + index * (group + 34 * unit), 28 * unit
            pygame.draw.circle(surface, INK, (x + 15 * unit, y), 15 * unit)
            pygame.draw.circle(surface, sprites.tint(color, .35), (x + 15 * unit, y), 12 * unit)
            self.text(surface, str(player.player_id), (x + 15 * unit, y - unit), 22, centered=True)
            for point in range(goal):
                center = (x + (50 + point * 22) * unit, y)
                pygame.draw.circle(surface, INK, center, 9 * unit)
                pygame.draw.circle(surface, color if point < self.scores[player.player_id] else sprites.WHITE, center, 6.5 * unit)
            if self.names.get(player.player_id):
                self.text(surface, self.names[player.player_id], (x + (50 + (goal - 1) * 11) * unit, y + 28 * unit), 22, centered=True)
            if player.powerup:
                pop = juice.pops.get(player.player_id, 0)
                slot = (x + 15 * unit, y + 34 * unit)
                if pop > 0:
                    pygame.draw.circle(surface, color, slot, (16 + 26 * (1 - pop)) * unit, max(1, round(5 * unit * pop)))
                grown = round(1 + 1.1 * pop * pop, 1)
                self.stamp(surface, self.sprite(("slot", player.powerup, grown), lambda: sprites.pickup(11 * unit * grown, player.powerup)), slot)
        if self.blocked:
            box = pygame.FRect(self.width * .15, self.height * .38, self.width * .7, self.height * .22)
            pygame.draw.rect(surface, sprites.CREAM, box, border_radius=max(1, round(22 * unit)))
            pygame.draw.rect(surface, INK, box, max(1, round(4 * unit)), border_radius=max(1, round(22 * unit)))
            self.text(surface, self.error, (self.width / 2, self.height * .45), 44, centered=True)
            self.text(surface, "Erase or move a physical obstacle", (self.width / 2, self.height * .55), 26, centered=True)
        elif self.phase != "playing":
            if juice.banner is None:
                juice.banner, juice.banner_age = (f"{self.name(self.winner)} WINS!" if self.winner else "DRAW!", juice.color(self.winner), self.phase == "match_over"), 1.0
            juice.draw_banner(surface, self)
        screen.blit(frame, (0, 0))
