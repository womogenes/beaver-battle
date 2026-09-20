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
    {"name": "TWIN LAKES", "rocks": [(.30, .53, .042), (.30, .40, .034), (.30, .66, .034), (.41, .31, .028), (.41, .75, .028)],
     "ponds": [(.27, .25, .12, .15), (.27, .81, .12, .15), (.20, .53, .05, .14)]},
    {"name": "THE MOAT", "rocks": [(.31, .40, .034), (.31, .66, .034), (.21, .53, .036), (.40, .22, .028), (.40, .84, .028)],
     "ponds": [(.5, .53, .19, .30)], "islands": [(.5, .53, .085, .14)]},
    {"name": "ROCK GARDEN", "rocks": [(.20, .30, .03), (.20, .53, .03), (.20, .76, .03), (.29, .41, .03), (.29, .65, .03),
                                      (.38, .30, .03), (.38, .53, .034), (.38, .76, .03), (.445, .42, .024), (.445, .64, .024)],
     "ponds": [(.12, .22, .06, .10), (.12, .84, .06, .10), (.29, .20, .05, .07), (.29, .86, .05, .07), (.29, .53, .05, .13), (.44, .53, .04, .10)]},
    {"name": "THE RIVER", "rocks": [(.24, .17, .026), (.36, .88, .026), (.42, .45, .028), (.42, .61, .028), (.17, .53, .03)],
     "ponds": [(.30, .16, .022, .12), (.30, .36, .06, .14), (.30, .56, .075, .14), (.30, .76, .06, .14), (.30, .93, .022, .08)]},
    {"name": "HORSESHOE", "rocks": [(.5 - .125 * math.cos(math.radians(a)), .53 + .222 * math.sin(math.radians(a)), .03) for a in (-52, -26, 0, 26, 52)]
     + [(.24, .30, .03), (.24, .76, .03)],
     "ponds": [(.5, .22, .10, .12), (.5, .84, .10, .12), (.30, .50, .05, .15), (.43, .30, .05, .08), (.43, .76, .05, .08)]},
    {"name": "STEPPING STONES", "rocks": [(.25, .67, .028), (.38, .37, .028), (.155, .53, .026), (.45, .66, .024)],
     "ponds": [(.25, .33, .085, .24), (.38, .75, .085, .24), (.12, .86, .06, .10)]},
    {"name": "BOULDER WALL", "rocks": [(.26, y, .03) for y in (.19, .42, .53, .64, .75, .86)] + [(.40, y, .028) for y in (.20, .31, .42, .53, .64, .86)],
     "ponds": [(.32, .31, .05, .09), (.455, .75, .045, .09)]},
    {"name": "THE MARSH", "rocks": [(.23, .53, .03), (.34, .36, .028), (.34, .70, .028), (.43, .53, .026)],
     "ponds": [(.17, .34, .06, .10), (.17, .72, .06, .10), (.28, .53, .04, .10), (.39, .20, .06, .09), (.39, .86, .06, .09), (.44, .40, .03, .06), (.44, .66, .03, .06)]},
]


def mirrored(items):
    return [item for item in items] + [(1 - item[0], *item[1:]) for item in items if abs(item[0] - .5) > 1e-6]


def scattered(board, shrink):
    """Shrink a board's rocks and ponds and give each a smaller sibling, so the meadow is a fine-grained
    scatter rather than a few big lumps. Anything that would crowd a start pad or the chest is left out."""
    def clear(x, y, reach):
        return all(math.hypot((x - px) * 16 / 9, y - .53) > reach + .075 for px in (.085, .5)) and .16 < y < .93 and .13 < x <= .5

    rocks, ponds = [], []
    for index, (x, y, r) in enumerate(board["rocks"]):
        rocks.append((x, y, r * shrink))
        side, lift = (.052 if index % 2 else -.052), (.118 if index % 3 else -.118)
        if clear(x + side, y + lift, r * shrink):
            rocks.append((x + side, y + lift, r * shrink * .8))
    for index, (x, y, rx, ry) in enumerate(board.get("ponds", [])):
        ponds.append((x, y, rx * shrink, ry * shrink))
        side, lift = (-.062 if index % 2 else .062), (-.13 if index % 2 else .13)
        if clear(x + side, y + lift, max(rx, ry * 9 / 16) * shrink):
            ponds.append((x + side, y + lift, rx * shrink * .7, ry * shrink * .7))
    islands = [(x, y, rx * shrink, ry * shrink) for x, y, rx, ry in board.get("islands", [])]
    return rocks, ponds, islands


def cheapest_route(passable, cost, starts, goal, cell):
    """Dijkstra over a grid. passable and cost are 2-D arrays; starts are cells; goal is a bool grid.

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
        for dy, dx, step in ((1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)):
            ny, nx = y + dy, x + dx
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
    water: np.ndarray | None = None
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
        self.rng = random.Random(self.setting("seed", 7) + self.board_index + 1)
        self.board_index = (self.board_index + 1) % len(BOARDS) if board is None else board % len(BOARDS)
        self.board = BOARDS[self.board_index]
        self.bots, self.art, self.particles, self.sounds = set(bots), {}, [], []
        self.chest = pygame.Vector2(self.width * (.915 if solo else .5), self.height * .53)
        rocks, ponds, islands = scattered(self.board, self.setting("object_scale", .62))
        self.rocks = [(pygame.Vector2(x * self.width, y * self.height), r * self.width) for x, y, r in mirrored(rocks)]
        layers = []
        for shapes in (ponds, islands):
            layer = np.zeros((self.height, self.width), np.uint8)
            for x, y, rx, ry in shapes:
                cv2.ellipse(layer, (round(x * self.width), round(y * self.height)), (round(rx * self.width), round(ry * self.height)), 0, 0, 360, 1, -1)
            layers.append(layer.astype(bool) | layer.astype(bool)[:, ::-1])  # Mirrored to the pixel, so neither side is favoured.
        self.water = layers[0] & ~layers[1]
        self.runners = {}
        for player_id, x in zip(ids, (.085, .915)):
            start = pygame.Vector2(x * self.width, self.height * .53)
            self.runners[player_id] = Runner(player_id, start, start.copy(), np.zeros((self.height, self.width), bool), facing=1 if x < .5 else -1)
        self.winner, self.verdict, self.clock, self.camera_ink = None, "", 0.0, False
        # With one cursor the humans take turns to draw; with markers on a board everybody draws at once.
        self.drawing = [player_id for player_id in ids if player_id not in self.bots]
        self.begin_drawing()

    def begin_drawing(self):
        self.phase, self.timer = "drawing", self.setting("draw_seconds", 30)
        self.sounds.append("go")
        for player_id in self.bots:
            runner = self.runners[player_id]
            if runner.plan is None:
                runner.plan = self.bot_plan(runner)

    # ---- the board -------------------------------------------------------------------------------------------------

    def in_water(self, point):
        x, y = int(point.x), int(point.y)
        return 0 <= x < self.width and 0 <= y < self.height and bool(self.water[y, x])

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
        free = np.ones((rows, columns), bool)
        for center, radius in self.rocks:
            free &= np.hypot(xs * cell + cell / 2 - center.x, ys * cell + cell / 2 - center.y) > radius + 16 * self.scale
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
        route, missed = cheapest_route(small, cost, starts, goal & small, cell)
        if route is None:
            return None, missed
        return smoothed([runner.start.copy()] + route[::3] + [self.chest.copy()], 2), None

    def judge(self, camera_ink=None):
        self.phase, self.timer = "checking", self.setting("check_seconds", 2.6)
        for runner in self.runners.values():
            ink = camera_ink if camera_ink is not None and runner.player_id not in self.bots else runner.ink
            runner.route, runner.broken_at = self.find_route(runner, ink)
            runner.state = "waiting" if runner.route else "broken"
            if runner.route:
                runner.marks = [0.0]
                for first, second in zip(runner.route, runner.route[1:]):
                    runner.marks.append(runner.marks[-1] + first.distance_to(second))
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
        if self.phase == "drawing":
            self.update_drawing(dt, inputs, ink)
        elif self.phase == "checking":
            self.timer -= dt
            if self.timer <= 0:
                self.start_running()
        elif self.phase == "running":
            self.update_running(dt, inputs)

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
            if target <= runner.travelled:
                continue
            pace = self.setting("swim_speed" if swimming else "walk_speed", 48 if swimming else 68) * self.scale
            pace *= self.setting("boost_factor", 2.2) if runner.boost > 0 else 1
            pace *= self.setting("solo_pace", 1.5) if self.solo else 1  # Twice the distance, so a brisker beaver.
            ahead = min(target, runner.travelled + pace * dt)
            place = self.point_at(runner, ahead)
            if self.rock_at(place, 5 * self.scale) is not None:
                runner.state = "stuck"
                self.sounds.append("bonk")
                continue
            if place.x != runner.pos.x:
                runner.facing = 1 if place.x > runner.pos.x else -1
            runner.stride += (ahead - runner.travelled)
            if runner.stride > (26 if swimming else 20) * self.scale:
                runner.stride = 0
                self.sounds.append("paddle" if swimming else "step")
                if swimming:
                    self.splash(place, 7)
            runner.travelled, runner.pos = ahead, place
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
        surface = sprites.tiled_ground("grass.jpg", self.width, self.height, round(330 * unit), lighten=.12)
        pond = sprites.tiled_ground("water.jpg", self.width, self.height, round(620 * unit), lighten=.18)
        mask = self.water.astype(np.uint8) * 255
        bank = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (round(18 * unit) | 1, round(18 * unit) | 1)))
        for layer, alpha in ((None, bank), (pond, mask)):
            sheet = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            if layer is None:
                sheet.fill((226, 214, 160))
            else:
                sheet.blit(layer, (0, 0))
            pixels = pygame.surfarray.pixels_alpha(sheet)
            pixels[:] = cv2.GaussianBlur(alpha, (5, 5), 0).T
            del pixels
            surface.blit(sheet, (0, 0))
        for index, (center, radius) in enumerate(self.rocks):
            rock = sprites.boulder(radius * .95, index + self.board_index * 17)
            surface.blit(rock, rock.get_rect(center=center))
        return surface

    def draw(self, surface):
        unit = self.scale
        frame = self.sprite(("frame",), lambda: pygame.Surface((self.width, self.height), 0, 24))
        frame.blit(self.sprite(("ground",), self.ground), (0, 0))
        colors = {player_id: sprites.PLAYER_COLORS[player_id - 1] for player_id in self.runners}
        for runner in self.runners.values():
            color = colors[runner.player_id]
            pygame.draw.circle(frame, sprites.INK, runner.start, 26 * unit)
            pygame.draw.circle(frame, sprites.tint(color, .35), runner.start, 22 * unit)
            if not self.camera_ink or runner.player_id in self.bots:
                for wide, shade in ((10, sprites.INK), (6, color)):
                    for first, second in runner.strokes:
                        pygame.draw.line(frame, shade, first, second, max(2, round(wide * unit)))
                        pygame.draw.circle(frame, shade, second, max(1, round(wide * unit / 2)))
            if runner.route and self.phase != "drawing":
                # The line the beaver will actually walk, including any little breaks that were joined up.
                for distance in range(0, int(runner.marks[-1]), max(6, round(18 * unit))):
                    done = distance <= runner.travelled
                    pygame.draw.circle(frame, sprites.WHITE if done else sprites.INK, self.point_at(runner, distance), max(2, round((4 if done else 3) * unit)))
            if runner.state == "broken" and runner.broken_at is not None:
                cross = sprites.label("X", max(20, round(64 * unit)), sprites.PINK_DARK)
                frame.blit(cross, cross.get_rect(center=runner.broken_at))
        chest = self.sprite(("chest", self.phase == "match_over" and self.winner is not None),
                            lambda: sprites.chest(48 * unit, open_lid=self.phase == "match_over" and self.winner is not None))
        frame.blit(chest, chest.get_rect(center=self.chest + pygame.Vector2(0, -4 * unit * abs(math.sin(self.clock * 2.4)))))
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
            who = f"{self.name(turn)}: " if turn is not None else ""
            message = f"{who}DRAW YOUR PATH TO THE CHEST   {max(0, math.ceil(self.timer))}"
        elif self.phase == "checking":
            message = "CHECKING THE PATHS..."
        elif self.phase == "running":
            message = "TRACE YOUR PATH WITH YOUR LASER!"
        else:
            message = ""
        title = sprites.sign(self.board["name"], max(14, round(24 * unit)), sprites.BLUE_BRIGHT)
        frame.blit(title, title.get_rect(midleft=(16 * unit, bar.centery)))
        if message:
            line = sprites.sign(message, max(16, round(32 * unit)))
            frame.blit(line, line.get_rect(center=(self.width / 2, bar.centery)))
        if self.solo and self.phase in ("running", "checking", "drawing"):
            best = f"BEST TODAY  {self.standings[0][1]:.2f}  {self.standings[0][0]}" if self.standings else "NO TIME YET TODAY"
            words = sprites.sign(f"{self.race_time:5.1f} s" if self.phase == "running" else best, max(14, round((34 if self.phase == "running" else 22) * unit)),
                                 sprites.BLUE if self.phase == "running" else sprites.BLUE_BRIGHT)
            frame.blit(words, words.get_rect(midright=(self.width - 128 * unit, bar.centery)))
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
