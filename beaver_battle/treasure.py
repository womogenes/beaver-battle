"""Treasure Dash: draw a route to the chest, then trace it with your laser to walk your beaver there.

Like game.py this is independent of cameras and controllers. Ink is either drawn with the aim point
while fire is held (a laptop cursor) or handed in as the camera's bool mask of marker on the board.
"""

from dataclasses import dataclass, field
import heapq
import math
import random

import cv2
import numpy as np
import pygame

from beaver_battle import sprites

# Boards are written for the left half and mirrored, so both players face the same puzzle.
# Rocks are (x, y, radius) and ponds and islands (x, y, rx, ry), all as fractions of the board width
# (radii) or of width and height (positions). An island is land cut back out of the ponds.
BOARDS = [
    # Two or three ponds to a board, no more: a mirrored pair, and sometimes one in the middle. Ponds are drawn at
    # their written size; only the rocks are shrunk and scattered.
    {"name": "TWIN LAKES", "rocks": [(.30, .53, .042), (.30, .40, .034), (.30, .66, .034), (.41, .31, .028), (.41, .75, .028)],
     "ponds": [(.24, .30, .10, .17)]},
    # The chest sits on an island. Swim the moat, or go the long way round to a portal that lands beside it.
    {"name": "THE MOAT", "logs": 3, "rocks": [(.31, .40, .034), (.31, .66, .034), (.21, .53, .036), (.40, .22, .028), (.40, .84, .028),
                                              (.15, .34, .03), (.15, .72, .03), (.25, .22, .03), (.24, .84, .03)],
     "ponds": [(.5, .53, .20, .33)], "islands": [(.5, .53, .16, .27)], "portals": [((.30, .22), (.43, .53))]},
    {"name": "ROCK GARDEN", "rocks": [(.20, .30, .03), (.20, .53, .03), (.20, .76, .03), (.29, .41, .03), (.29, .65, .03),
                                      (.38, .30, .03), (.38, .76, .03), (.445, .42, .024), (.445, .64, .024)],
     "ponds": [(.37, .53, .045, .13)]},
    {"name": "THE RIVER", "rocks": [(.24, .17, .026), (.36, .88, .026), (.42, .45, .028), (.42, .61, .028), (.17, .53, .03)],
     "ponds": [(.30, .15, .03, .12), (.30, .34, .045, .13), (.30, .54, .055, .14), (.30, .74, .045, .13), (.30, .92, .03, .10)],
     "portals": [((.19, .86), (.41, .84))]},
    {"name": "HORSESHOE", "rocks": [(.5 - .125 * math.cos(math.radians(a)), .53 + .222 * math.sin(math.radians(a)), .03) for a in (-52, -26, 0, 26, 52)]
     + [(.24, .30, .03), (.24, .76, .03)],
     "ponds": [(.5, .20, .10, .10), (.5, .86, .10, .10)]},
    {"name": "STEPPING STONES", "rocks": [(.25, .74, .028), (.38, .37, .028), (.155, .53, .026), (.45, .66, .024)],
     "ponds": [(.27, .42, .075, .22)]},
    {"name": "BOULDER WALL", "rocks": [(.26, y, .03) for y in (.19, .42, .53, .64, .75, .86)] + [(.40, y, .028) for y in (.20, .31, .42, .53, .64, .86)],
     "ponds": [(.33, .31, .055, .10)], "portals": [((.16, .22), (.445, .25))]},
    {"name": "THE MARSH", "rocks": [(.23, .53, .03), (.34, .36, .028), (.34, .70, .028), (.43, .53, .026)],
     "ponds": [(.29, .53, .05, .14), (.5, .18, .12, .09)]},
]

# One player crosses the whole board, so these need not be symmetric: written edge to edge, never mirrored.
SOLO_BOARDS = [
    # Ponds here are drawn at their written size (no shrinking), as broad connected water.
    {"name": "THE BIG RIVER", "whole": True, "deer": 2,
     "rocks": [(.25, .40, .04), (.30, .72, .04), (.74, .34, .04), (.70, .70, .04), (.38, .20, .035), (.62, .86, .035)],
     "ponds": [(.50, .08, .07, .14), (.47, .26, .085, .16), (.52, .46, .095, .17), (.48, .66, .09, .17), (.53, .86, .08, .17),
               (.28, .86, .11, .10), (.38, .90, .09, .08), (.74, .14, .10, .09)],
     "portals": [((.30, .20), (.70, .88))]},
    {"name": "DEER PARK", "whole": True, "deer": 6, "boosts": 3,
     "rocks": [(.22, .30, .035), (.30, .74, .035), (.52, .16, .04), (.70, .76, .035), (.80, .32, .035)],
     "ponds": [(.50, .52, .15, .21), (.40, .40, .10, .14), (.60, .66, .11, .15), (.50, .80, .07, .13), (.50, .95, .06, .10),
               (.30, .30, .07, .08), (.72, .50, .08, .07)]},
    {"name": "PORTAL WOODS", "whole": True, "deer": 3, "trees": 8, "logs": 4,
     "rocks": [(.30, .53, .035), (.72, .50, .035), (.46, .84, .034), (.56, .20, .034), (.18, .38, .032), (.40, .16, .03),
               (.63, .82, .032), (.84, .38, .03), (.52, .68, .03), (.26, .70, .03)],
     "ponds": [(.22, .12, .10, .12), (.32, .24, .09, .12), (.42, .36, .09, .12), (.51, .49, .09, .12), (.60, .62, .09, .12),
               (.69, .75, .09, .12), (.79, .88, .10, .12)],
     "portals": [((.20, .80), (.62, .30)), ((.40, .66), (.80, .66))]},
]

def mirrored(items):
    return [item for item in items] + [(1 - item[0], *item[1:]) for item in items if abs(item[0] - .5) > 1e-6]


def scattered(board, shrink):
    """Shrink a board's rocks and ponds and give each a smaller sibling, so the meadow is a fine-grained
    scatter rather than a few big lumps. Anything that would crowd a start pad or the chest is left out."""
    whole = board.get("whole", False)

    def clear(x, y, reach):
        pads = (.085, .915) if whole else (.085, .5)
        return all(math.hypot((x - px) * 16 / 9, y - .53) > reach + .075 for px in pads) and .16 < y < .93 and .13 < x <= (.87 if whole else .5)

    rocks, ponds = [], []
    sizes = (1.0, 1.55, .7, 1.25, .6, 1.4, .85)  # Pebbles to boulders, in a fixed order so a duel stays mirrored.
    for index, (x, y, r) in enumerate(board["rocks"]):
        rocks.append((x, y, r * shrink * sizes[index % len(sizes)]))
        side, lift = (.052 if index % 2 else -.052), (.118 if index % 3 else -.118)
        if clear(x + side, y + lift, r * shrink):
            rocks.append((x + side, y + lift, r * shrink * sizes[(index + 3) % len(sizes)] * .8))
    for index, (x, y, rx, ry) in enumerate(board.get("ponds", [])):
        ponds.append((x, y, rx, ry))  # Water is drawn at its written size, with no stray puddles scattered round it.
    islands = [(x, y, rx * shrink, ry * shrink) for x, y, rx, ry in board.get("islands", [])]
    return rocks, ponds, islands


def cheapest_route(passable, cost, starts, goal, cell, hops=None):
    """Dijkstra over a grid. passable and cost are 2-D arrays; starts are cells; goal is a bool grid.
    hops maps a cell to the cell a portal sends it to, for next to nothing.

    Returns the route as board points, or None with the cell that came closest to the goal.
    """
    height, width = passable.shape
    best = np.full(passable.shape, np.inf)
    came = {}
    queue = []
    for y, x in starts:
        if 0 <= y < height and 0 <= x < width and passable[y, x]:
            best[y, x] = 0
            heapq.heappush(queue, (0.0, y, x))
    goal_cells = np.argwhere(goal)
    center = goal_cells.mean(axis=0) if len(goal_cells) else np.array([0, 0])
    nearest, gap = None, math.inf
    while queue:
        spent, y, x = heapq.heappop(queue)
        if spent > best[y, x]:
            continue
        distance = math.hypot(y - center[0], x - center[1])
        if distance < gap:
            nearest, gap = (y, x), distance
        if goal[y, x]:
            route = [(y, x)]
            while route[-1] in came:
                route.append(came[route[-1]])
            return [pygame.Vector2((cx + .5) * cell, (cy + .5) * cell) for cy, cx in reversed(route)], None
        moves = [(y + dy, x + dx, step) for dy, dx, step in ((1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))]
        if hops and (y, x) in hops:
            moves.append((*hops[(y, x)], .05))
        for ny, nx, step in moves:
            if 0 <= ny < height and 0 <= nx < width and passable[ny, nx]:
                total = spent + step * cost[ny, nx]
                if total < best[ny, nx]:
                    best[ny, nx] = total
                    came[(ny, nx)] = (y, x)
                    heapq.heappush(queue, (total, ny, nx))
    return None, (pygame.Vector2((nearest[1] + .5) * cell, (nearest[0] + .5) * cell) if nearest else None)


def smoothed(points, rounds=2):
    for repeat in range(rounds):
        if len(points) < 3:
            break
        rounded = [points[0]]
        for first, second in zip(points, points[1:]):
            rounded += [first * .75 + second * .25, first * .25 + second * .75]
        points = rounded + [points[-1]]
    return points


def segment_distance(point, first, second):
    along = second - first
    if not along.length_squared():
        return point.distance_to(first)
    return point.distance_to(first + along * max(0.0, min(1.0, (point - first).dot(along) / along.length_squared())))


@dataclass
class Deer:
    pos: pygame.Vector2
    target: pygame.Vector2
    wait: float = 0.0
    facing: float = 1.0
    pace: float = 0.0  # Current speed: a deer gathers itself, bounds, and eases to a stop.
    hop: float = 0.0  # How far through the current bound it is, in bounds.


@dataclass
class Runner:
    player_id: int
    start: pygame.Vector2
    pos: pygame.Vector2
    ink: np.ndarray
    strokes: list = field(default_factory=list)
    pen: pygame.Vector2 | None = None
    route: list | None = None
    marks: list = field(default_factory=list)
    broken_at: pygame.Vector2 | None = None
    travelled: float = 0.0
    state: str = "waiting"  # waiting, walking, swimming, stuck, broken, home
    boost: float = 0.0
    cooldown: float = 0.0
    special_held: bool = False
    facing: float = 1.0
    stride: float = 0.0
    plan: list | None = None
    drawn: float = 0.0
    lapse: float = 0.0
    jumps: list = field(default_factory=list)  # Distances along the route at which a portal is taken.
    stall: float = 0.0  # Seconds left winded after running into a deer.
    shy: float = 0.0  # Seconds during which deer cannot stall this beaver again.


@dataclass
class Treasure:
    config: dict
    width: int = 1280
    height: int = 720
    scale: float = 1.0
    phase: str = "drawing"  # drawing, checking, running, match_over
    clock: float = 0.0
    timer: float = 0.0
    board_index: int = -1
    board: dict = field(default_factory=dict)
    rocks: list = field(default_factory=list)
    trees: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    portals: list = field(default_factory=list)
    boosts: list = field(default_factory=list)
    deer: list = field(default_factory=list)
    matches: int = 0
    water: np.ndarray | None = None
    shore: np.ndarray | None = None
    chest: pygame.Vector2 = field(default_factory=pygame.Vector2)
    runners: dict = field(default_factory=dict)
    drawing: list = field(default_factory=list)
    bots: set = field(default_factory=set)
    names: dict = field(default_factory=dict)
    winner: int | None = None
    verdict: str = ""
    sounds: list = field(default_factory=list)
    particles: list = field(default_factory=list)
    art: dict = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    camera_ink: bool = False
    solo: bool = False
    race_time: float = 0.0
    standings: list = field(default_factory=list)  # Today's best on this board, filled in by the launcher.
    rank: int | None = None

    def setting(self, name, default):
        return self.config.get("treasure", {}).get(name, default)

    def name(self, player_id):
        return self.names.get(player_id) or f"PLAYER {player_id}"

    def new_match(self, player_ids=(1, 2), bots=(), board=None, solo=False):
        """Two players race from the two edges to a chest in the middle. Solo, one player crosses the
        whole board, edge to edge, against the clock."""
        ids = list(player_ids)[:1 if solo else 2]
        if len(ids) != (1 if solo else 2):
            raise ValueError("Treasure Dash is for two players, or one playing solo")
        self.solo, self.race_time, self.standings, self.rank = solo, 0.0, [], None
        game = self.config.get("game", {})
        self.width, self.height = int(game.get("width", 1280)), int(game.get("height", 720))
        self.scale = min(self.width / 1280, self.height / 720)
        boards = self.board_list(solo)
        self.board_index = (self.board_index + 1) % len(boards) if board is None else board % len(boards)
        self.board = boards[self.board_index]
        whole = self.board.get("whole", False)
        self.matches += 1
        # One player's boards are the same for everyone, so today's times compare; a duel is dressed afresh each match.
        self.rng = random.Random(self.setting("seed", 7) + self.board_index * 101 + (0 if solo else self.matches))
        self.bots, self.art, self.particles, self.sounds = set(bots), {}, [], []
        self.chest = pygame.Vector2(self.width * (.915 if solo else .5), self.height * .53)
        rocks, ponds, islands = scattered(self.board, self.setting("object_scale", .62))
        self.rocks = [(pygame.Vector2(x * self.width, y * self.height), r * self.width) for x, y, r in (rocks if whole else mirrored(rocks))]
        layers = []
        for shapes in (ponds, islands):
            layer = np.zeros((self.height, self.width), np.uint8)
            for x, y, rx, ry in shapes:
                cv2.ellipse(layer, (round(x * self.width), round(y * self.height)), (round(rx * self.width), round(ry * self.height)), 0, 0, 360, 1, -1)
            # A duel is mirrored to the pixel, so neither side is favoured.
            layers.append(layer.astype(bool) if whole else layer.astype(bool) | layer.astype(bool)[:, ::-1])
        self.water = layers[0] & ~layers[1]
        # Water plus a margin: what a deer, or a tree, treats as too close to the bank.
        margin = round(30 * self.scale) | 1
        self.shore = cv2.dilate(self.water.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (margin, margin))).astype(bool)
        self.portals = []
        for index, ((ax, ay), (bx, by)) in enumerate(self.board.get("portals", [])):
            for flip in ((False,) if whole else (False, True)):
                ends = [pygame.Vector2((1 - x if flip else x) * self.width, y * self.height) for x, y in ((ax, ay), (bx, by))]
                self.portals.append((ends[0], ends[1], index))
        self.runners = {}
        for player_id, x in zip(ids, (.085, .915)):
            start = pygame.Vector2(x * self.width, self.height * .53)
            self.runners[player_id] = Runner(player_id, start, start.copy(), np.zeros((self.height, self.width), bool), facing=1 if x < .5 else -1)
        self.winner, self.verdict, self.clock, self.camera_ink = None, "", 0.0, False
        self.decorate(whole)
        # With one cursor the humans take turns to draw; with markers on a board everybody draws at once.
        self.drawing = [player_id for player_id in ids if player_id not in self.bots]
        self.begin_drawing()

    def board_list(self, solo):
        """Two players share eight mirrored boards. One player picks from three maps, each with its own leaderboard."""
        return SOLO_BOARDS if solo else BOARDS

    def decorate(self, whole):
        """Scatter trees, logs, boost clusters and deer. In a duel the fixed things are placed on one half
        and mirrored; deer run about, so they are simply dropped anywhere. Nothing may seal a beaver in."""
        unit, rng = self.scale, self.rng
        self.trees, self.logs, self.boosts, self.deer = [], [], [], []
        pads = [runner.start for runner in self.runners.values()] + [self.chest, pygame.Vector2(self.width - self.chest.x, self.chest.y)]

        def spot(reach, everywhere=False, dry=False):
            for attempt in range(60):
                point = pygame.Vector2(rng.uniform(.14, .86 if whole or everywhere else .5) * self.width, rng.uniform(.18, .92) * self.height)
                crowded = any(point.distance_to(pad) < 100 * unit + reach for pad in pads) or any(point.distance_to(end) < 60 * unit + reach for a, b, index in self.portals for end in (a, b))
                if not crowded and self.blocked_at(point, reach + 34 * unit) is None and not (dry and self.near_water(point)):
                    return point
            return None

        def twins(point):
            return [point] if whole else [point, pygame.Vector2(self.width - point.x, point.y)]

        half = 2 if whole else 1
        for index in range(self.board.get("trees", self.setting("trees", 3)) * half):
            radius = rng.uniform(24, 32) * unit
            point = spot(radius, dry=True)
            if point is not None:
                self.trees += [(twin, radius) for twin in twins(point)]
                if not self.passable():
                    del self.trees[-len(twins(point)):]
        for index in range(self.board.get("logs", self.setting("logs", 2)) * half):
            point, angle, reach = spot(24 * unit), rng.uniform(0, math.pi), rng.uniform(55, 80) * unit
            arm = pygame.Vector2(math.cos(angle), math.sin(angle)) * reach
            # A long log only needs its own length clear, not a whole circle of that radius.
            if point is not None and any(self.blocked_at(point + arm * step, 30 * unit) is not None or
                                         any((point + arm * step).distance_to(pad) < 95 * unit for pad in pads) for step in (-1, -.5, .5, 1)):
                point = None
            if point is not None:
                pairs = [(point - arm, point + arm)] if whole else [(point - arm, point + arm), (pygame.Vector2(self.width - (point - arm).x, (point - arm).y), pygame.Vector2(self.width - (point + arm).x, (point + arm).y))]
                self.logs += [(first, second, 7 * unit) for first, second in pairs]
                if not self.passable():
                    del self.logs[-len(pairs):]
        self.bar_the_way(whole, pads)
        for index in range(self.board.get("boosts", self.setting("boost_clusters", 2)) * half):
            point, angle = spot(30 * unit), rng.uniform(0, math.tau)
            if point is not None:
                count = self.setting("berries_per_cluster", 1)
                for step in range(count):
                    token = point + pygame.Vector2(math.cos(angle), math.sin(angle)) * (step - (count - 1) / 2) * 30 * unit
                    self.boosts += [[twin, True] for twin in twins(token)]
        for index in range(self.board.get("deer", self.setting("deer", 2))):
            point = spot(30 * unit, everywhere=True, dry=True)
            if point is not None:
                self.deer.append(Deer(point, point.copy(), rng.uniform(0, 2)))

    def legs(self, runner):
        """Every stretch a lazy route could take in one stroke: start to chest, start to a portal, a portal to the chest."""
        ends = [end for first, second, index in self.portals for end in (first, second)]
        pairs = [(runner.start, self.chest)] + [(runner.start, end) for end in ends] + [(end, self.chest) for end in ends]
        return [(first, second) for first, second in pairs if first.distance_to(second) > 200 * self.scale]

    def beeline(self, first, second, bow, lean=1.0, steps=90):
        """A lazy route between two places: the straight line, bowed sideways by `bow` pixels. `lean` moves the top of
        the bow toward one end or the other, so a single early or late swerve counts as lazy too."""
        across = second - first
        side = pygame.Vector2(-across.y, across.x).normalize()
        return [first + across * (step / steps) + side * bow * math.sin(math.pi * (step / steps) ** lean) for step in range(steps + 1)]

    def open_beelines(self, runner):
        """The straightish strokes, out of a fan of bows on every leg, that meet no rock, tree or stick on the way."""
        found = []
        for first, second in self.legs(runner):
            reach = min(self.setting("beeline_bow", 170) * self.scale, .3 * first.distance_to(second))
            found += [(first, second, bow, lean) for lean in (1.0, .55, 1.8) for bow in np.linspace(-reach, reach, 15)
                      if all(self.blocked_at(point) is None for point in self.beeline(first, second, bow, lean)[6:-6])]
        return found

    def bar_the_way(self, whole, pads):
        """No board may be won with straight strokes: wherever one is still open, to the chest or to and from a portal,
        a stick is laid across it (mirrored in a duel), as long as some real route to the chest is left."""
        unit, rng = self.scale, self.rng
        runner = next(iter(self.runners.values()))
        for attempt in range(160):
            clear = self.open_beelines(runner)
            if not clear:
                return
            first, second, bow, lean = clear[len(clear) // 2] if attempt % 2 else rng.choice(clear)
            route = self.beeline(first, second, bow, lean)
            point = route[round(len(route) * rng.uniform(.4, .78))]  # Late on the line, so it tempts before it stops you.
            ahead = (second - first).normalize()
            arm = pygame.Vector2(-ahead.y, ahead.x).rotate(rng.uniform(-20, 20)) * rng.uniform(55, 85) * unit
            ends = [point - arm, point + arm]
            if any(end.distance_to(pad) < 95 * unit for end in ends + [point] for pad in pads) or \
                    any(segment_distance(end, *ends) < 50 * unit for a, b, index in self.portals for end in (a, b)):
                continue
            pairs = [tuple(ends)] if whole else [tuple(ends), tuple(pygame.Vector2(self.width - end.x, end.y) for end in ends)]
            self.logs += [(first, second, 7 * unit) for first, second in pairs]
            if not self.passable():
                del self.logs[-len(pairs):]

    def passable(self):
        return all(len(self.bot_plan(runner)) > 2 for runner in self.runners.values())

    def blocked_at(self, point, slack=0.0):
        """A rock, a tree trunk or a fallen stick in the way. Art is rough, so a line may graze an edge and get by."""
        for center, radius in self.rocks:
            if point.distance_to(center) < radius * .82 + slack:
                return center
        for center, radius in self.trees:
            if point.distance_to(center) < radius * .62 + slack:
                return center
        for first, second, half in self.logs:
            if segment_distance(point, first, second) < half * .9 + slack:
                return (first + second) / 2
        return None

    def begin_drawing(self):
        self.phase, self.timer = "drawing", self.setting("draw_seconds", 15)
        self.sounds.append("go")
        for player_id in self.bots:
            runner = self.runners[player_id]
            if runner.plan is None:
                runner.plan = self.bot_plan(runner)

    # ---- the board -------------------------------------------------------------------------------------------------

    def in_water(self, point):
        x, y = int(point.x), int(point.y)
        return 0 <= x < self.width and 0 <= y < self.height and bool(self.water[y, x])

    def near_water(self, point):
        x, y = int(point.x), int(point.y)
        return not (0 <= x < self.width and 0 <= y < self.height) or bool(self.shore[y, x])

    def rock_at(self, point, slack=0.0):
        """Rocks are drawn rough, so a line may graze the edge of one and still get by."""
        for center, radius in self.rocks:
            if point.distance_to(center) < radius * .82 + slack:
                return center
        return None

    def bot_plan(self, runner):
        """A sensible human-looking route: around the rocks, preferring land, with a wobble."""
        cell = 8
        rows, columns = self.height // cell, self.width // cell
        ys, xs = np.mgrid[0:rows, 0:columns]
        solid = np.zeros((self.height, self.width), np.uint8)
        margin = 16 * self.scale
        for center, radius in self.rocks:
            cv2.circle(solid, (round(center.x), round(center.y)), round(radius + margin), 1, -1)
        for center, radius in self.trees:
            cv2.circle(solid, (round(center.x), round(center.y)), round(radius * .62 + margin), 1, -1)
        for first, second, half in self.logs:
            cv2.line(solid, (round(first.x), round(first.y)), (round(second.x), round(second.y)), 1, round(2 * (half + margin)))
        free = solid[cell // 2::cell, cell // 2::cell][:rows, :columns] == 0
        free[:round(70 * self.scale / cell)] = False
        free[-2:], free[:, :2], free[:, -2:] = False, False, False
        # A bot weighs water by what it really costs in time, so it swims when swimming is quicker.
        wet = self.setting("walk_speed", 66) / max(1, self.setting("swim_speed", 46))
        cost = np.where(self.water[::cell, ::cell][:rows, :columns], wet * self.rng.uniform(.9, 1.25), 1.0)
        goal = np.hypot(xs * cell - self.chest.x, ys * cell - self.chest.y) < 26 * self.scale
        route, missed = cheapest_route(free, cost, [(int(runner.start.y // cell), int(runner.start.x // cell))], goal, cell)
        if not route:
            return [runner.start, self.chest]
        route = smoothed(route[::4] + [self.chest], 3)
        phase = self.rng.uniform(0, math.tau)
        wobbly = []
        for index, point in enumerate(route):
            ahead = route[min(index + 1, len(route) - 1)] - route[max(index - 1, 0)]
            side = pygame.Vector2(-ahead.y, ahead.x).normalize() if ahead.length_squared() else pygame.Vector2()
            wobbly.append(point + side * 5 * self.scale * math.sin(phase + index * .35))
        return wobbly

    # ---- ink and routes ---------------------------------------------------------------------------------------------

    def scribble(self, runner, start, end):
        cv2.line(runner.ink.view(np.uint8), (round(start.x), round(start.y)), (round(end.x), round(end.y)), 1, max(3, round(7 * self.scale)))
        runner.strokes.append((start.copy(), end.copy()))

    def find_route(self, runner, ink):
        """Turn ink into the line a beaver walks: bridge small breaks, then follow it from start to chest.

        A break wider than merge_gap leaves the chest unreachable and that player loses.
        """
        cell = 4
        reach = max(1, round(self.setting("merge_gap", 34) * self.scale / 2))
        mask = cv2.dilate(ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1)))
        small = cv2.resize(mask, (self.width // cell, self.height // cell), interpolation=cv2.INTER_AREA) > 0
        # Walking the middle of the ink, not its edge: cells near the centre of the stroke are cheaper.
        depth = cv2.distanceTransform(small.astype(np.uint8), cv2.DIST_L2, 3)
        cost = 1 + 6 / (depth + .5)
        ys, xs = np.mgrid[0:small.shape[0], 0:small.shape[1]]
        home = np.hypot(xs * cell - runner.start.x, ys * cell - runner.start.y) < 46 * self.scale
        goal = np.hypot(xs * cell - self.chest.x, ys * cell - self.chest.y) < 40 * self.scale
        starts = [tuple(cell_index) for cell_index in np.argwhere(home & small)]
        if not starts:
            return None, runner.start.copy()
        # Ink that reaches one end of a portal carries on from ink at the other end.
        hops = {}
        for first, second, index in self.portals:
            for here, there in ((first, second), (second, first)):
                inside = np.argwhere((np.hypot(xs * cell - here.x, ys * cell - here.y) < 24 * self.scale) & small)
                landing = np.argwhere((np.hypot(xs * cell - there.x, ys * cell - there.y) < 24 * self.scale) & small)
                if len(inside) and len(landing):
                    arrive = min(landing, key=lambda spot: math.hypot(spot[1] * cell - there.x, spot[0] * cell - there.y))
                    hops.update({tuple(spot): (int(arrive[0]), int(arrive[1])) for spot in inside})
        route, missed = cheapest_route(small, cost, starts, goal & small, cell, hops)
        if route is None:
            return None, missed
        # A hop shows up as two neighbours far apart: keep the pieces separate so smoothing cannot bridge them.
        pieces = [[route[0]]]
        for before, after in zip(route, route[1:]):
            if before.distance_to(after) > cell * 3:
                pieces.append([])
            pieces[-1].append(after)
        pieces[0].insert(0, runner.start.copy())
        pieces[-1].append(self.chest.copy())
        line, runner.jumps = [], []
        for piece in pieces:
            if line:
                runner.jumps.append(len(line) - 1)  # The segment from this index to the next is the hop.
            line += smoothed([piece[0]] + piece[1:-1:3] + [piece[-1]], 2) if len(piece) > 2 else piece
        return line, None

    def judge(self, camera_ink=None):
        self.phase, self.timer = "checking", self.setting("check_seconds", 2.6)
        for runner in self.runners.values():
            ink = camera_ink if camera_ink is not None and runner.player_id not in self.bots else runner.ink
            runner.route, runner.broken_at = self.find_route(runner, ink)
            runner.state = "waiting" if runner.route else "broken"
            if runner.route:
                runner.marks = [0.0]
                for index, (first, second) in enumerate(zip(runner.route, runner.route[1:])):
                    runner.marks.append(runner.marks[-1] + (0.0 if index in runner.jumps else first.distance_to(second)))
                runner.jumps = [runner.marks[index] for index in runner.jumps]
        self.sounds.append("broken" if any(runner.state == "broken" for runner in self.runners.values()) else "ready")

    def point_at(self, runner, distance):
        marks, route = runner.marks, runner.route
        distance = max(0.0, min(distance, marks[-1]))
        index = max(0, min(len(marks) - 2, int(np.searchsorted(marks, distance, side="right")) - 1))
        span = marks[index + 1] - marks[index]
        return route[index].lerp(route[index + 1], (distance - marks[index]) / span if span else 0)

    # ---- the simulation ---------------------------------------------------------------------------------------------

    def update(self, dt, inputs, ink=None):
        """inputs maps player ids to PlayerInput. ink is the camera's marker mask, or None for cursor drawing."""
        del self.sounds[:-32]
        self.clock += dt
        self.camera_ink = ink is not None
        for particle in self.particles:
            particle[0] += particle[2] * dt
            particle[1] += particle[3] * dt
            particle[3] += 420 * self.scale * dt
            particle[4] -= dt
        self.particles = [particle for particle in self.particles if particle[4] > 0][-400:]
        if self.phase != "match_over":
            self.update_deer(dt)
        if self.phase == "drawing":
            self.update_drawing(dt, inputs, ink)
        elif self.phase == "checking":
            self.timer -= dt
            if self.timer <= 0:
                self.start_running()
        elif self.phase == "running":
            self.update_running(dt, inputs)

    def update_deer(self, dt):
        """Deer dart from one patch of grass to the next with short pauses to look about. They go where they like."""
        unit = self.scale
        pads = [runner.start for runner in self.runners.values()] + [self.chest]
        for deer in self.deer:
            if deer.wait > 0:
                deer.wait -= dt
                deer.pace, deer.hop = 0.0, 0.0
                continue
            gap = deer.target - deer.pos
            if gap.length() < 3 * unit:
                deer.wait = self.rng.uniform(.25, 1.3)
                for attempt in range(12):
                    angle, reach = self.rng.uniform(0, math.tau), self.rng.uniform(110, 340) * unit
                    target = deer.pos + pygame.Vector2(math.cos(angle), math.sin(angle)) * reach
                    roomy = 60 * unit < target.x < self.width - 60 * unit and 100 * unit < target.y < self.height - 50 * unit
                    paces = max(2, int(reach / (10 * unit)))  # Every 10 px, so no pond or rock slips between the samples.
                    way = [deer.pos.lerp(target, step / paces) for step in range(1, paces + 1)]
                    if roomy and not any(self.near_water(point) or self.blocked_at(point, 22 * unit) is not None for point in way) \
                            and all(target.distance_to(pad) > 90 * unit for pad in pads):
                        deer.target = target
                        break
                continue
            deer.facing = 1 if gap.x >= 0 else -1
            # Build up to full speed and slow for the last stretch, rather than starting and stopping dead.
            top = self.setting("deer_speed", 80) * unit
            wanted = top * min(1.0, .25 + gap.length() / (70 * unit))
            deer.pace += max(-320 * unit * dt, min(260 * unit * dt, wanted - deer.pace))
            step = min(gap.length(), deer.pace * dt)
            deer.pos += gap.normalize() * step
            deer.hop += step / (34 * unit)  # One bound for every 34 px covered, so the hops match the ground.

    def update_drawing(self, dt, inputs, ink):
        before = math.ceil(self.timer)
        self.timer -= dt
        if 0 < math.ceil(self.timer) < before and self.timer <= 5:
            self.sounds.append("tick")
        for player_id in self.bots:
            # A bot sketches its plan over a few seconds, like someone who has already decided.
            runner = self.runners[player_id]
            total = sum(first.distance_to(second) for first, second in zip(runner.plan, runner.plan[1:]))
            runner.drawn = min(1.0, runner.drawn + dt / self.setting("bot_draw_seconds", 5))
            walked = 0.0
            for first, second in zip(runner.plan, runner.plan[1:]):
                step = first.distance_to(second)
                if walked + step <= runner.drawn * total and (first, second) not in runner.strokes:
                    self.scribble(runner, first, second)
                walked += step
        turn = self.drawing[0] if self.drawing and ink is None else None
        if turn is not None:
            runner, control = self.runners[turn], inputs.get(turn)
            aim = pygame.Vector2(control.aim) if control is not None and control.aim is not None else None
            if aim is not None and control.fire and aim.y > 64 * self.scale:
                if runner.pen is not None and runner.pen.distance_to(aim) > 1:
                    self.scribble(runner, runner.pen, aim)
                runner.pen = aim
            else:
                runner.pen = None
            finished = control is not None and control.special and not runner.special_held and runner.ink.any()
            runner.special_held = bool(control is not None and control.special)
            if finished:
                self.timer = 0
        if self.timer <= 0:
            if turn is not None and len(self.drawing) > 1:
                self.drawing.pop(0)
                self.begin_drawing()
            elif all(self.runners[player_id].drawn >= 1 for player_id in self.bots):
                self.judge(ink)

    def start_running(self):
        ready = [runner for runner in self.runners.values() if runner.route]
        if len(ready) < len(self.runners):
            # A path that never reaches the chest loses on the spot.
            self.finish(ready[0].player_id if len(ready) == 1 else None, "BROKEN PATH")
            return
        self.phase, self.timer = "running", self.setting("solo_run_limit" if self.solo else "run_limit", 150 if self.solo else 75)
        self.sounds.append("go")

    def update_running(self, dt, inputs):
        self.timer -= dt
        self.race_time += dt
        for runner in self.runners.values():
            if runner.state in ("stuck", "home"):
                continue
            runner.boost, runner.cooldown = max(0.0, runner.boost - dt), max(0.0, runner.cooldown - dt)
            runner.shy = max(0.0, runner.shy - dt)
            if runner.stall > 0:
                # Winded by a deer: nothing to do but wait it out.
                runner.stall = max(0.0, runner.stall - dt)
                runner.shy = self.setting("deer_grace", 1.2)
                continue
            if runner.shy == 0 and any(deer.pos.distance_to(runner.pos) < 32 * self.scale for deer in self.deer):
                # A collision is a collision whoever was moving: a deer bounding into a standing beaver winds it too.
                runner.stall = self.setting("deer_stall", 2.0)
                self.sounds.append("bleat")
                self.sparkle(runner.pos, 18)
                continue
            if runner.player_id in self.bots:
                aim, special = self.bot_trace(runner, dt)
            else:
                control = inputs.get(runner.player_id)
                aim = pygame.Vector2(control.aim) if control is not None and control.aim is not None else None
                # In the race either button is the boost: a click on a laptop, button 1 or 2 on a controller.
                special = bool(control is not None and (control.special or control.fire))
            if special and not runner.special_held and runner.cooldown == 0:
                runner.boost, runner.cooldown = self.setting("boost_seconds", .8), self.setting("boost_cooldown", 3.5)
                self.sounds.append("boost")
            runner.special_held = special
            swimming = self.in_water(runner.pos)
            if swimming != (runner.state == "swimming"):
                self.sounds.append("plunge" if swimming else "step")
                if swimming:
                    self.splash(runner.pos, 14)
            runner.state = "swimming" if swimming else "walking"
            # The beaver only moves while the laser is tracing the line just ahead of it.
            target = self.traced(runner, aim)
            for mark in runner.jumps:
                # A beaver that reaches a portal goes through on its own. Tracing cannot lead it across: the line
                # just ahead is at the far pad, where nobody's laser is yet, and it would wait at the edge for ever.
                if 0 <= mark - runner.travelled <= 10 * self.scale:
                    target = max(target, mark + 3 * self.scale)
            if target <= runner.travelled:
                continue
            pace = self.setting("swim_speed" if swimming else "walk_speed", 48 if swimming else 68) * self.scale
            pace *= self.setting("boost_factor", 2.2) if runner.boost > 0 else 1
            pace *= self.setting("solo_pace", 1.5) if self.solo else 1  # Twice the distance, so a brisker beaver.
            ahead = min(target, runner.travelled + pace * dt)
            place = self.point_at(runner, ahead)
            if self.blocked_at(place, 5 * self.scale) is not None:
                runner.state = "stuck"
                self.sounds.append("bonk")
                continue
            if runner.shy == 0 and any(deer.pos.distance_to(place) < 30 * self.scale for deer in self.deer):
                # Run into a deer and you are winded for a couple of seconds; then you may push past it.
                runner.stall = self.setting("deer_stall", 2.0)
                self.sounds.append("bleat")
                self.sparkle(place, 10)
                continue
            for mark in runner.jumps:
                if runner.travelled <= mark < ahead:
                    self.sounds.append("warp")
                    for end in (self.point_at(runner, mark - .5), place):
                        self.sparkle(end, 16)
            if place.x != runner.pos.x:
                runner.facing = 1 if place.x > runner.pos.x else -1
            runner.stride += (ahead - runner.travelled)
            if runner.stride > (26 if swimming else 20) * self.scale:
                runner.stride = 0
                self.sounds.append("paddle" if swimming else "step")
                if swimming:
                    self.splash(place, 7)
            runner.travelled, runner.pos = ahead, place
            for token in self.boosts:
                if token[1] and token[0].distance_to(place) < 24 * self.scale:
                    token[1] = False
                    runner.boost = max(runner.boost, self.setting("token_boost", 1.2))
                    self.sounds.append("zip")
                    self.sparkle(token[0], 10)
            if runner.travelled >= runner.marks[-1] - 1:
                runner.state = "home"
                self.finish(runner.player_id, f"{self.race_time:.2f} s" if self.solo else "TREASURE!")
                return
        if all(runner.state == "stuck" for runner in self.runners.values()):
            self.finish(None, "STUCK ON A ROCK" if self.solo else "BOTH STUCK ON ROCKS")
        elif self.timer <= 0 and self.solo:
            self.finish(None, "OUT OF TIME")
        elif self.timer <= 0:
            closest = sorted(self.runners.values(), key=lambda runner: runner.marks[-1] - runner.travelled)
            self.finish(closest[0].player_id, "TIME! CLOSEST WINS")

    def traced(self, runner, aim):
        """How far along the route the laser is pointing, within reach of the beaver; never backwards."""
        if aim is None:
            return runner.travelled
        best, reach = runner.travelled, self.setting("trace_reach", 150) * self.scale
        for step in range(0, int(reach), max(4, round(6 * self.scale))):
            distance = runner.travelled + step
            if distance > runner.marks[-1]:
                distance = runner.marks[-1]
            if self.point_at(runner, distance).distance_to(aim) <= self.setting("trace_width", 46) * self.scale:
                best = distance
        return best

    def bot_trace(self, runner, dt):
        runner.lapse -= dt
        if runner.lapse < -self.rng.uniform(1.4, 2.6):
            runner.lapse = self.rng.uniform(.15, .4)  # Even a good tracer wanders off the line now and then.
        if runner.lapse > 0:
            return None, False
        return self.point_at(runner, runner.travelled + 60 * self.scale), runner.cooldown == 0 and not self.in_water(runner.pos)

    def splash(self, point, count):
        for index in range(count):
            angle = self.rng.uniform(-math.pi, 0)
            speed = self.rng.uniform(60, 200) * self.scale
            self.particles.append([point.x, point.y, speed * math.cos(angle), speed * math.sin(angle), self.rng.uniform(.3, .6), self.rng.uniform(3, 6) * self.scale])

    def sparkle(self, point, count):
        for index in range(count):
            angle, speed = self.rng.uniform(0, math.tau), self.rng.uniform(80, 240) * self.scale
            self.particles.append([point.x, point.y, speed * math.cos(angle), speed * math.sin(angle) - 120 * self.scale, self.rng.uniform(.3, .6), self.rng.uniform(3, 5) * self.scale])

    def finish(self, winner, verdict):
        self.phase, self.winner, self.verdict = "match_over", winner, verdict
        self.sounds.append("treasure" if winner is not None else "broken")

    # ---- drawing ----------------------------------------------------------------------------------------------------

    def sprite(self, key, build):
        if key not in self.art:
            self.art[key] = build()
        return self.art[key]

    def ground(self):
        """Grass everywhere, the water wallpaper inside the ponds with a bank around them, and the rocks."""
        unit = self.scale
        # The daisy meadow, shown whole rather than tiled, and washed toward white for a lighter green.
        surface = sprites.tiled_ground("grass.jpg", self.width, self.height, self.width, lighten=self.setting("grass_wash", .35))
        pond = sprites.tiled_ground("water.jpg", self.width, self.height, round(620 * unit), lighten=.18)
        mask = self.water.astype(np.uint8) * 255
        # A thin bank of long grass round every pond and river, and the water inside it.
        reach = round(14 * unit) | 1
        bank = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
        blades = sprites.tiled_ground("grass_blades.jpg", self.width, self.height, round(150 * unit))
        for layer, alpha in ((blades, bank), (pond, mask)):
            sheet = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            sheet.blit(layer, (0, 0))
            pixels = pygame.surfarray.pixels_alpha(sheet)
            pixels[:] = cv2.GaussianBlur(alpha, (5, 5), 0).T
            del pixels
            surface.blit(sheet, (0, 0))
        # Lily pads float on the bigger ponds, well clear of the banks. They are only there to look at.
        rng = random.Random(self.board_index * 13 + 5)
        depth = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        count, bodies = cv2.connectedComponents(mask)
        for body in range(1, count):
            deep = np.argwhere((bodies == body) & (depth > 30 * unit))
            if (bodies == body).sum() < 26000 * unit ** 2 or not len(deep):
                continue
            placed = []
            for attempt in range(60):
                y, x = deep[rng.randrange(len(deep))]
                if all(math.hypot(x - px, y - py) > 58 * unit for px, py in placed) and len(placed) < 2 + (bodies == body).sum() // (42000 * unit ** 2):
                    placed.append((x, y))
                    pad = sprites.lily_pad(rng.uniform(15, 21) * unit, degrees=rng.uniform(0, 360), flower=rng.random() < .45)
                    surface.blit(pad, pad.get_rect(center=(int(x), int(y))))
        for first, second, half in self.logs:
            along = second - first
            image = sprites.stick(round(along.length() / unit), round(2 * half / unit), unit, seed=round(first.x + first.y))
            image = pygame.transform.rotozoom(image, -math.degrees(math.atan2(along.y, along.x)), 1)
            surface.blit(image, image.get_rect(center=(first + second) / 2))
        for index, (center, radius) in enumerate(self.rocks):
            rock = sprites.boulder(radius * .95, index + self.board_index * 17)
            surface.blit(rock, rock.get_rect(center=center))
        for index, (center, radius) in enumerate(self.trees):
            canopy = sprites.tree(radius, index + self.board_index * 7)
            surface.blit(canopy, canopy.get_rect(center=center))
        return surface

    def draw(self, surface):
        unit = self.scale
        frame = self.sprite(("frame",), lambda: pygame.Surface((self.width, self.height), 0, 24))
        frame.blit(self.sprite(("ground",), self.ground), (0, 0))
        colors = {player_id: sprites.PLAYER_COLORS[player_id - 1] for player_id in self.runners}
        for first, second, index in self.portals:
            # A pair of portals shares a colour and spins the same way, so it is plain which leads where.
            shade = (sprites.LAVENDER, sprites.PINK, sprites.BUTTER)[index % 3]
            for end in (first, second):
                for ring, reach in enumerate((26, 18, 10)):
                    pygame.draw.circle(frame, sprites.INK, end, (reach + 3) * unit)
                    pygame.draw.circle(frame, shade if ring % 2 == 0 else sprites.WHITE, end, reach * unit)
                for spoke in range(3):
                    angle = self.clock * 3.2 + spoke * math.tau / 3
                    pygame.draw.circle(frame, sprites.INK, end + pygame.Vector2(math.cos(angle), math.sin(angle)) * 18 * unit, 5 * unit)
                    pygame.draw.circle(frame, sprites.WHITE, end + pygame.Vector2(math.cos(angle), math.sin(angle)) * 18 * unit, 3 * unit)
        token = self.sprite(("token",), lambda: sprites.berries(17 * unit, color=self.setting("berry_color", "red")))
        for index, (place, alive) in enumerate(self.boosts):
            if alive:
                frame.blit(token, token.get_rect(center=place + pygame.Vector2(0, 3 * unit * math.sin(self.clock * 5 + index))))
        for runner in self.runners.values():
            color = colors[runner.player_id]
            pygame.draw.circle(frame, sprites.INK, runner.start, 26 * unit)
            pygame.draw.circle(frame, sprites.tint(color, .35), runner.start, 22 * unit)
            chosen = runner.route and self.phase != "drawing"
            if not chosen and (not self.camera_ink or runner.player_id in self.bots):
                # While drawing (or when no path reached the chest) every stroke is shown, scribbles and all.
                for wide, shade in ((10, sprites.INK), (6, color)):
                    for first, second in runner.strokes:
                        pygame.draw.line(frame, shade, first, second, max(2, round(wide * unit)))
                        pygame.draw.circle(frame, shade, second, max(1, round(wide * unit / 2)))
            if chosen:
                # Once judged, only the one path the beaver will walk is drawn: spare strokes and dead ends vanish,
                # little breaks appear joined, and a portal hop is simply not drawn.
                step = max(3, round(5 * unit))
                spots = [self.point_at(runner, distance) for distance in range(0, int(runner.marks[-1]) + step, step)]
                for wide, shade in ((10, sprites.INK), (6, color)):
                    for first, second in zip(spots, spots[1:]):
                        if first.distance_to(second) < step * 3:
                            pygame.draw.line(frame, shade, first, second, max(2, round(wide * unit)))
                            pygame.draw.circle(frame, shade, second, max(1, round(wide * unit / 2)))
                for distance in range(0, int(min(runner.travelled, runner.marks[-1])), max(6, round(18 * unit))):
                    pygame.draw.circle(frame, sprites.WHITE, self.point_at(runner, distance), max(2, round(3 * unit)))
            if runner.state == "broken" and runner.broken_at is not None:
                cross = sprites.label("X", max(20, round(64 * unit)), sprites.PINK_DARK)
                frame.blit(cross, cross.get_rect(center=runner.broken_at))
        chest = self.sprite(("chest", self.phase == "match_over" and self.winner is not None),
                            lambda: sprites.chest(48 * unit, open_lid=self.phase == "match_over" and self.winner is not None))
        frame.blit(chest, chest.get_rect(center=self.chest + pygame.Vector2(0, -4 * unit * abs(math.sin(self.clock * 2.4)))))
        for deer in self.deer:
            image = self.sprite(("deer", deer.facing), lambda: pygame.transform.flip(sprites.deer(26 * unit), deer.facing < 0, False))
            effort = min(1.0, deer.pace / max(1.0, self.setting("deer_speed", 80) * unit))
            lift = abs(math.sin(math.pi * deer.hop)) * 11 * unit * effort
            # A shadow that shrinks as it leaves the ground, a lean into the run, and a breath while it stands.
            shadow = pygame.Rect(0, 0, (34 - lift) * unit, (11 - lift * .35) * unit)
            shadow.center = (deer.pos.x, deer.pos.y + 26 * unit)
            pygame.draw.ellipse(frame, (132, 176, 140), shadow)
            if effort > .05:
                image = pygame.transform.rotozoom(image, -deer.facing * 9 * effort * math.cos(math.pi * deer.hop), 1)
            else:
                breath = 1 + .025 * math.sin(self.clock * 2.6 + id(deer) % 7)
                image = pygame.transform.smoothscale(image, (image.get_width(), round(image.get_height() * breath)))
            frame.blit(image, image.get_rect(midbottom=(deer.pos.x, deer.pos.y + 30 * unit - lift)))
        for runner in self.runners.values():
            self.draw_beaver(frame, runner, colors[runner.player_id])
        for x, y, vx, vy, life, size in self.particles:
            pygame.draw.circle(frame, sprites.INK, (x, y), size + unit)
            pygame.draw.circle(frame, sprites.SKY_LIGHT, (x, y), size)
        self.draw_hud(frame, colors)
        surface.blit(frame, (0, 0))

    def draw_beaver(self, frame, runner, color):
        unit = self.scale
        bob = math.sin(self.clock * (9 if runner.state == "walking" else 5) + runner.player_id) * (2.5 if runner.state in ("walking", "swimming") else 0) * unit
        center = runner.pos + pygame.Vector2(0, bob)
        if runner.state == "swimming":
            ring = self.sprite(("ring", color), lambda: sprites.swimmer(11 * unit, color))
            ring = pygame.transform.flip(ring, runner.facing < 0, False)
            frame.blit(ring, ring.get_rect(center=center))
        if runner.boost > 0:
            for index in range(3):
                pygame.draw.circle(frame, sprites.BUTTER, center - pygame.Vector2(runner.facing * (22 + index * 13) * unit, 0), (7 - index * 2) * unit)
        head = self.sprite(("tim", runner.state == "swimming"), lambda: sprites.tim(15 * unit, paws=runner.state != "swimming"))
        frame.blit(head, head.get_rect(center=center))
        short = self.name(runner.player_id).replace(" ", "")[:3] if self.names.get(runner.player_id) else str(runner.player_id)
        word = sprites.lettering(short, max(12, round(19 * unit)), sprites.INK)
        plate = pygame.Rect(0, 0, word.get_width() + 16 * unit, 24 * unit)
        plate.center = center + pygame.Vector2(0, -34 * unit)
        pygame.draw.rect(frame, sprites.INK, plate.inflate(5 * unit, 5 * unit), border_radius=round(14 * unit))
        pygame.draw.rect(frame, sprites.tint(color, .45), plate, border_radius=round(12 * unit))
        frame.blit(word, word.get_rect(center=plate.center))
        if runner.stall > 0:
            # Seeing stars, with the seconds left.
            for index in range(3):
                angle = self.clock * 5 + index * math.tau / 3
                spot = center + pygame.Vector2(math.cos(angle) * 20, -22 + math.sin(angle) * 7) * unit
                pygame.draw.circle(frame, sprites.INK, spot, 5 * unit)
                pygame.draw.circle(frame, sprites.BUTTER, spot, 3.4 * unit)
            oops = sprites.sign(f"OOF!  {runner.stall:.1f}", max(14, round(24 * unit)))
            frame.blit(oops, oops.get_rect(center=center + pygame.Vector2(0, 30 * unit)))
        if runner.state == "stuck":
            stuck = sprites.sign("STUCK!", max(14, round(26 * unit)))
            frame.blit(stuck, stuck.get_rect(center=center + pygame.Vector2(0, 30 * unit)))

    def draw_hud(self, frame, colors):
        unit = self.scale
        bar = pygame.Rect(0, 0, self.width, round(58 * unit))
        pygame.draw.rect(frame, sprites.CREAM, bar)
        pygame.draw.line(frame, sprites.INK, bar.bottomleft, bar.bottomright, max(2, round(3 * unit)))
        if self.phase == "drawing":
            turn = self.drawing[0] if self.drawing and not self.camera_ink else None
            who = f"{self.name(turn)}: " if turn is not None and not self.solo else ""
            message = f"{who}DRAW YOUR PATH TO THE CHEST"
        elif self.phase == "checking":
            message = "CHECKING THE PATHS..."
        elif self.phase == "running":
            message = "TRACE YOUR PATH WITH YOUR LASER!"
        else:
            message = ""
        title = sprites.sign(self.board["name"], max(14, round(24 * unit)), sprites.BLUE_BRIGHT)
        frame.blit(title, title.get_rect(midleft=(16 * unit, bar.centery)))
        # The header is three things in a row: the board's name, the message, and (solo) the time. The message takes
        # what is left between the other two, shrinking if it has to, so nothing is ever written over anything else.
        left_edge, right_edge = title.get_width() + 40 * unit, self.width - 128 * unit
        if self.solo and self.phase in ("running", "checking", "drawing"):
            best = f"BEST TODAY  {self.standings[0][1]:.2f}  {self.standings[0][0]}" if self.standings else "NO TIME YET TODAY"
            words = sprites.sign(f"{self.race_time:5.1f} s" if self.phase == "running" else best, max(14, round((34 if self.phase == "running" else 22) * unit)),
                                 sprites.BLUE if self.phase == "running" else sprites.BLUE_BRIGHT)
            words = sprites.fitted(words, 300 * unit)
            frame.blit(words, words.get_rect(midright=(right_edge, bar.centery)))
            right_edge -= words.get_width() + 24 * unit
        if message:
            line = sprites.fitted(sprites.sign(message, max(16, round(32 * unit))), right_edge - left_edge)
            frame.blit(line, line.get_rect(center=((left_edge + right_edge) / 2, bar.centery)))
        if self.phase == "drawing":
            sprites.time_bar(frame, (self.width * .2, bar.bottom + 12 * unit, self.width * .6, 18 * unit), self.timer / self.setting("draw_seconds", 15), self.clock)
        if self.phase == "running":
            for index, runner in enumerate(self.runners.values()):
                # Boost meter: full means button 2 will fire it.
                meter = pygame.Rect(0, 0, 120 * unit, 16 * unit)
                meter.midleft = (runner.start.x - 60 * unit, runner.start.y + 46 * unit)
                ready = 1 - runner.cooldown / self.setting("boost_cooldown", 3.5)
                pygame.draw.rect(frame, sprites.INK, meter.inflate(6 * unit, 6 * unit), border_radius=round(10 * unit))
                pygame.draw.rect(frame, sprites.WHITE, meter, border_radius=round(8 * unit))
                pygame.draw.rect(frame, sprites.BUTTER if ready >= 1 else colors[runner.player_id], (meter.x, meter.y, meter.width * ready, meter.height), border_radius=round(8 * unit))
                word = sprites.sign("BOOST", max(12, round(18 * unit)))
                frame.blit(word, word.get_rect(center=(meter.centerx, meter.bottom + 14 * unit)))
        hint = ""
        if self.phase == "drawing" and self.drawing and not self.camera_ink:
            hint = "Hold the left button to draw.  Right click when you are done."
        elif self.phase == "running" and not self.camera_ink:
            hint = "Keep the mouse on your line, just ahead of your beaver.  Click to boost!"
        if hint:
            words = sprites.sign(hint, max(14, round(24 * unit)))
            frame.blit(words, words.get_rect(center=(self.width / 2, self.height - 26 * unit)))
        if self.phase == "match_over":
            veil = pygame.Surface(frame.get_size(), pygame.SRCALPHA)
            veil.fill((*sprites.INK, 90))
            frame.blit(veil, (0, 0))
            finished = self.solo and self.winner is not None
            plate = pygame.Rect(0, 0, self.width * .62, self.height * (.30 if finished else .42))
            plate.center = (self.width / 2, self.height * (.27 if finished else .42))
            pygame.draw.ellipse(frame, sprites.INK, plate.inflate(12 * unit, 12 * unit))
            pygame.draw.ellipse(frame, sprites.CREAM, plate)
            color = colors.get(self.winner, sprites.BUTTER)
            if finished:
                heading, verdict = self.verdict, ("NEW BEST TODAY!" if self.rank == 1 else f"NUMBER {self.rank} TODAY" if self.rank else self.board["name"])
            else:
                heading = ("TRY AGAIN!" if self.solo else f"{self.name(self.winner)} WINS!" if self.winner is not None else "NOBODY WINS!")
                verdict = self.verdict
            title = sprites.label(heading, max(24, round((104 if finished else 120) * unit)), color, tilt=-4)
            if title.get_width() > plate.width * .88:
                title = pygame.transform.rotozoom(title, 0, plate.width * .88 / title.get_width())
            frame.blit(title, title.get_rect(center=(plate.centerx, plate.centery - (16 if finished else 26) * unit)))
            line = sprites.lettering(verdict, max(14, round((34 if finished else 44) * unit)), sprites.BLUE)
            frame.blit(line, line.get_rect(center=(plate.centerx, plate.centery + (58 if finished else 78) * unit)))
            if finished and self.standings:
                # Today's table for this board, with this run picked out.
                table = pygame.Rect(0, 0, 520 * unit, (44 + 40 * len(self.standings)) * unit)
                table.midtop = (self.width / 2, plate.bottom + 16 * unit)
                pygame.draw.rect(frame, sprites.INK, table.inflate(10 * unit, 10 * unit), border_radius=round(26 * unit))
                pygame.draw.rect(frame, sprites.CREAM, table, border_radius=round(22 * unit))
                head = sprites.lettering(f"TODAY ON {self.board['name']}", max(12, round(24 * unit)), sprites.BLUE_BRIGHT)
                frame.blit(head, head.get_rect(center=(table.centerx, table.y + 24 * unit)))
                for place, (name, seconds) in enumerate(self.standings, 1):
                    y = table.y + (30 + 40 * place) * unit
                    mine = place == self.rank
                    if mine:
                        pygame.draw.rect(frame, sprites.tint(color, .45), (table.x + 12 * unit, y - 18 * unit, table.width - 24 * unit, 36 * unit), border_radius=round(16 * unit))
                    for words, x, anchor in ((str(place), table.x + 40 * unit, "center"), (name, table.x + 84 * unit, "midleft"), (f"{seconds:.2f} s", table.right - 28 * unit, "midright")):
                        image = sprites.lettering(words, max(12, round(28 * unit)), sprites.BLUE)
                        frame.blit(image, image.get_rect(**{anchor: (x, y)}))
