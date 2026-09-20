"""Dam It!: one player draws a dam to keep the river off the lodge, then the other chews through it.

Like game.py and treasure.py this knows nothing of cameras or controllers. The builder's wood is either
drawn with the aim point while fire is held (a laptop cursor) or handed in as the camera's marker mask.

The balance rests on one rule: a dam has a fixed total strength, shared out over all the wood in it in
proportion to area. Drawing more never adds strength, it spreads it thinner, so the work to breach a dam
depends only on what fraction of its wood lies across the breach. The valley is a funnel, wide where the
river enters and narrow by the lodge: a dam far from the lodge must be long and therefore weak, but the
water then has far to run; a dam by the lodge can be short and strong, but a breach floods at once.
"""

from dataclasses import dataclass, field
import math
import random

import cv2
import numpy as np
import pygame

from beaver_battle import sprites
from beaver_battle.game import closed_shapes

CELL = 8  # Water is simulated on a grid this coarse.


@dataclass
class DamIt:
    config: dict
    width: int = 1280
    height: int = 720
    scale: float = 1.0
    phase: str = "building"  # building, rising, attack, match_over
    timer: float = 0.0
    clock: float = 0.0
    rounds: int = 0
    builder: int = 1
    attacker: int = 2
    names: dict = field(default_factory=dict)
    solid: np.ndarray | None = None  # Valley walls: nothing passes.
    allowed: np.ndarray | None = None  # Where wood may be drawn.
    ink: np.ndarray | None = None  # What the builder has drawn so far.
    wood: np.ndarray | None = None  # The standing dam, once sealed.
    logs: np.ndarray | None = None  # Which of that wood belongs to closed shapes.
    sections: np.ndarray | None = None
    strength: dict = field(default_factory=dict)  # section -> [bites left, bites when whole]
    wet: np.ndarray | None = None
    flow: float = 0.0
    flooded: float = 0.0
    lodge: pygame.Vector2 = field(default_factory=pygame.Vector2)
    beaver: pygame.Vector2 = field(default_factory=pygame.Vector2)
    facing: float = -1.0
    bite: float = 0.0
    gnaw: float = 0.0
    chewing: int = 0
    shown: float = 0.0
    pen: pygame.Vector2 | None = None
    done_held: bool = False
    winner: int | None = None
    verdict: str = ""
    sounds: list = field(default_factory=list)
    particles: list = field(default_factory=list)
    art: dict = field(default_factory=dict)
    rng: random.Random = field(default_factory=lambda: random.Random(3))
    camera_ink: bool = False
    stale: bool = True

    def setting(self, name, default):
        return self.config.get("dam", {}).get(name, default)

    def name(self, player_id):
        return self.names.get(player_id) or f"PLAYER {player_id}"

    def new_match(self, player_ids=(1, 2), swap=None):
        """Roles swap every round unless told otherwise, so a pair of players each get both jobs."""
        ids = list(player_ids)[:2]
        if len(ids) != 2:
            raise ValueError("Dam It! is for exactly two players")
        game = self.config.get("game", {})
        self.width, self.height = int(game.get("width", 1280)), int(game.get("height", 720))
        self.scale = min(self.width / 1280, self.height / 720)
        swap = self.rounds % 2 == 1 if swap is None else swap
        self.builder, self.attacker = (ids[1], ids[0]) if swap else (ids[0], ids[1])
        self.rounds += 1
        w, h = self.width, self.height
        self.lodge = pygame.Vector2(.88 * w, .53 * h)
        self.beaver = pygame.Vector2(.965 * w, .53 * h)
        # A funnel: the river comes in across the whole left side and the banks close in toward the lodge.
        solid = np.zeros((h, w), np.uint8)
        top = [(0, 0), (w, 0), (w, .32 * h), (.72 * w, .32 * h), (0, .135 * h)]
        bottom = [(0, h), (w, h), (w, .74 * h), (.72 * w, .74 * h), (0, .925 * h)]
        for shape in (top, bottom):
            cv2.fillPoly(solid, [np.array(shape, np.int32)], 1)
        self.solid = solid.astype(bool)
        ys, xs = np.mgrid[0:h, 0:w]
        self.allowed = ~self.solid & (np.hypot(xs - self.lodge.x, ys - self.lodge.y) > self.setting("lodge_clearance", 105) * self.scale) \
            & (xs > 70 * self.scale) & (xs < .955 * w)
        self.ink = np.zeros((h, w), bool)
        self.wood, self.logs, self.sections, self.strength = None, None, None, {}
        self.wet = np.zeros((h // CELL, w // CELL), bool)
        self.wet[:, 0] = True
        self.flow, self.flooded, self.clock, self.bite, self.gnaw, self.chewing, self.shown = 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0
        self.winner, self.verdict, self.pen, self.done_held = None, "", None, False
        self.sounds, self.particles, self.art, self.stale, self.camera_ink = ["go"], [], {}, True, False
        self.phase, self.timer = "building", self.setting("build_seconds", 30)

    # ---- wood -------------------------------------------------------------------------------------------------------

    def wood_length(self, mask=None):
        """Wood is measured as the length of stroke it amounts to, so a filled shape costs what it covers."""
        mask = self.ink if mask is None else mask
        return float(mask.sum()) / max(1.0, self.setting("stroke", 10) * self.scale)

    def total_strength(self, length):
        """Bites in the whole dam. One clean wall's worth of wood (free_wood) gets the full strength; past that the
        dam as a whole gets weaker, so piling wood on is worse than useless: every piece is both a smaller share
        and a share of less."""
        spare = self.setting("free_wood", 450) * self.scale / max(1.0, length)
        return self.setting("strength", 270) * min(1.0, spare) ** self.setting("excess_penalty", 1.0)

    def bites_per_piece(self, mask=None):
        """What one section of a plain stroke would take to chew through if the dam were sealed now."""
        length = self.wood_length(mask)
        pieces = max(1.0, length / (self.setting("section", 40) * self.scale))
        return max(1.0, self.total_strength(length) / pieces)

    def scribble(self, start, end):
        if self.wood_length() >= self.setting("max_wood", 2600):
            return
        stroke = np.zeros(self.ink.shape, np.uint8)
        cv2.line(stroke, (round(start.x), round(start.y)), (round(end.x), round(end.y)), 1, max(3, round(self.setting("stroke", 10) * self.scale)))
        self.ink |= stroke.astype(bool) & self.allowed
        self.stale = True

    def seal(self, camera_ink=None):
        """Turn what was drawn into a dam: join hairline gaps, fill closed shapes as logs, cut it into sections,
        and share the dam's strength over them by area."""
        ink = (camera_ink if camera_ink is not None else self.ink) & self.allowed
        reach = max(1, round(self.setting("merge_gap", 14) * self.scale))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))
        # Closed against the valley walls as well, so a stroke that stops just short of the bank still seals.
        joined = cv2.morphologyEx((ink | self.solid).astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool) & ~self.solid
        joined &= self.allowed | cv2.dilate(ink.astype(np.uint8), kernel).astype(bool)
        filled, shapes = closed_shapes(joined, 5, 700 * self.scale ** 2, .12 * self.width * self.height)
        self.wood = filled & ~self.solid
        self.logs = np.zeros(self.wood.shape, np.uint8)
        for shape in shapes:
            cv2.drawContours(self.logs, [shape.contour], -1, 1, -1)
        self.logs = cv2.dilate(self.logs, kernel).astype(bool) & self.wood if shapes else self.logs.astype(bool)
        # Cut the dam into pieces about a section long. Seeds are spread through each connected run of wood, each new
        # one as far as possible from the rest, and every bit of wood joins its nearest seed. A stroke is therefore
        # cut across, into lengths of its full thickness, wherever on the board it was drawn; a fixed grid would
        # split a stroke lying along a grid line into two thin half-walls.
        # Thick wood is cut into pieces as wide as it is thick, so a slab is never layers to tunnel through: one
        # piece gone and the water is through it.
        count, labels = cv2.connectedComponents(self.wood.astype(np.uint8))
        size = max(8, round(self.setting("section", 40) * self.scale))
        self.sections = np.zeros(self.wood.shape, np.int32)
        depth = cv2.distanceTransform(self.wood.astype(np.uint8), cv2.DIST_L2, 3)
        span = min(301, 2 * int(depth.max()) + 3) | 1
        thickness = 2 * cv2.dilate(depth, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (span, span)))
        serial = 0
        for label in range(1, count):
            ys, xs = np.nonzero(labels == label)
            spots = np.column_stack([xs, ys]).astype(np.float32)
            # Seeds sit on the middle line of the wood, so the cuts between them run straight across it.
            middle = depth[ys, xs] >= .35 * thickness[ys, xs]
            first = int(middle.argmax())
            seeds = [spots[first]]
            nearest = np.hypot(*(spots - seeds[0]).T)
            owner = np.zeros(len(spots), np.int32)
            apart = np.maximum(size * .62, .9 * thickness[ys, xs])
            while np.where(middle, nearest / apart, 0).max() > 1 and len(seeds) < 400:
                seeds.append(spots[int(np.where(middle, nearest / apart, 0).argmax())])
                reach = np.hypot(*(spots - seeds[-1]).T)
                owner[reach < nearest] = len(seeds) - 1
                nearest = np.minimum(nearest, reach)
            self.sections[ys, xs] = serial + 1 + owner
            serial += len(seeds)
        # Any piece that still came out as a sliver would be worth a bite or two, a weak spot the builder never drew,
        # so it is folded into the neighbour it touches most.
        touch = np.ones((5, 5), np.uint8)
        for repeat in range(3):
            areas = np.bincount(self.sections.ravel())
            typical = np.median(areas[1:][areas[1:] > 0]) if (areas[1:] > 0).any() else 0
            for index in np.argsort(areas):
                if index == 0 or not 0 < areas[index] < .5 * typical:
                    continue
                piece = self.sections == index
                beside = self.sections[cv2.dilate(piece.astype(np.uint8), touch).astype(bool) & ~piece & self.wood]
                if len(beside):
                    self.sections[piece] = np.bincount(beside).argmax()
                else:
                    # A stray speck on its own, a crumb left where the drawing met the bank: not part of any dam.
                    self.sections[piece], self.wood[piece] = 0, False
        areas = np.bincount(self.sections.ravel())
        total = max(1, int(areas[1:].sum()))
        budget = self.total_strength(self.wood_length(self.wood))
        # The short piece where a wall meets the bank must not be the cheap way in: no piece counts for less than
        # most of a typical one.
        typical = float(np.median(areas[1:][areas[1:] > 0])) if (areas[1:] > 0).any() else 0.0
        self.strength = {index: [max(1.0, budget * max(areas[index], .75 * typical) / total)] * 2 for index in range(1, len(areas)) if areas[index]}
        self.stale = True
        self.phase, self.timer = "rising", self.setting("rise_seconds", 2.5)
        self.sounds.append("rush")

    def blocked_grid(self):
        block = (self.solid if self.wood is None else self.solid | self.wood)
        rows, columns = self.wet.shape
        return block[:rows * CELL, :columns * CELL].reshape(rows, CELL, columns, CELL).any(axis=(1, 3))

    # ---- the simulation ---------------------------------------------------------------------------------------------

    def update(self, dt, inputs, ink=None):
        del self.sounds[:-32]
        self.clock += dt
        self.camera_ink = ink is not None
        for particle in self.particles:
            particle[0] += particle[2] * dt
            particle[1] += particle[3] * dt
            particle[3] += 500 * self.scale * dt
            particle[4] -= dt
        self.particles = [particle for particle in self.particles if particle[4] > 0][-300:]
        if self.phase == "building":
            self.update_building(dt, inputs.get(self.builder), ink)
        elif self.phase == "rising":
            self.spread(self.setting("rise_speed", 1400) * dt)
            self.timer -= dt
            if self.timer <= 0:
                if self.lodge_flooded() >= self.setting("flood_goal", .5):
                    self.finish(self.attacker, "THE DAM LEAKED!")
                else:
                    self.phase, self.timer = "attack", self.setting("attack_seconds", 25)
                    self.sounds.append("go")
        elif self.phase == "attack":
            self.update_attack(dt, inputs.get(self.attacker))

    def update_building(self, dt, control, ink):
        before = math.ceil(self.timer)
        self.timer -= dt
        if 0 < math.ceil(self.timer) < before and self.timer <= 5:
            self.sounds.append("tick")
        if ink is None and control is not None and control.aim is not None:
            aim = pygame.Vector2(control.aim)
            if control.fire:
                if self.pen is not None and self.pen.distance_to(aim) > 1:
                    self.scribble(self.pen, aim)
                self.pen = aim
            else:
                self.pen = None
            if control.special and not self.done_held and self.ink.any():
                self.timer = 0
            self.done_held = bool(control.special)
        if self.timer <= 0:
            self.seal(ink)

    def spread(self, distance):
        """Water creeps into every open neighbour, so many cells a second. It never drains."""
        self.flow += distance / CELL
        blocked = None
        while self.flow >= 1:
            self.flow -= 1
            blocked = self.blocked_grid() if blocked is None else blocked
            grown = self.wet.copy()
            grown[1:] |= self.wet[:-1]
            grown[:-1] |= self.wet[1:]
            grown[:, 1:] |= self.wet[:, :-1]
            grown[:, :-1] |= self.wet[:, 1:]
            grown &= ~blocked
            if (grown == self.wet).all():
                self.flow = 0.0
                break
            self.wet = grown

    def lodge_flooded(self):
        rows, columns = self.wet.shape
        ys, xs = np.mgrid[0:rows, 0:columns]
        ring = np.hypot(xs * CELL - self.lodge.x, ys * CELL - self.lodge.y) < self.setting("lodge_ring", 70) * self.scale
        self.flooded = float(self.wet[ring].mean())
        return self.flooded

    def passable(self, point):
        x, y = int(point.x), int(point.y)
        if not (12 <= x < self.width - 12 and 70 * self.scale <= y < self.height - 12):
            return False
        return not self.solid[y, x] and not self.wood[y, x]

    def update_attack(self, dt, control):
        self.timer -= dt
        self.bite, self.gnaw, self.shown = max(0.0, self.bite - dt), max(0.0, self.gnaw - dt), max(0.0, self.shown - dt)
        if control is not None and control.aim is not None:
            gap = pygame.Vector2(control.aim) - self.beaver
            if gap.length() > 6 * self.scale:
                step = gap.normalize() * min(gap.length(), self.setting("beaver_speed", 200) * self.scale * dt)
                self.facing = 1 if gap.x >= 0 else -1
                # Wood and rock stop a beaver; it slides along them rather than sticking.
                for move in (step, pygame.Vector2(step.x, 0), pygame.Vector2(0, step.y)):
                    if move.length_squared() and self.passable(self.beaver + move):
                        self.beaver += move
                        break
            if (control.fire or control.special) and self.bite == 0:
                self.chew(pygame.Vector2(control.aim))
        self.spread(self.setting("flow_speed", 120) * self.scale * dt)
        if self.lodge_flooded() >= self.setting("flood_goal", .5):
            self.finish(self.attacker, "THE LODGE IS FLOODED!")
        elif self.timer <= 0:
            self.finish(self.builder, "THE LODGE STAYED DRY!")

    def chew(self, aim):
        """Bite the nearest wood within reach, favouring the side the laser is on."""
        reach = round(self.setting("bite_reach", 34) * self.scale)
        x0, y0 = max(0, int(self.beaver.x) - reach), max(0, int(self.beaver.y) - reach)
        window = self.sections[y0:int(self.beaver.y) + reach + 1, x0:int(self.beaver.x) + reach + 1]
        ys, xs = np.nonzero(window)
        if not len(ys):
            return
        lean = (aim - self.beaver)
        lean = lean.normalize() * 10 * self.scale if lean.length_squared() else pygame.Vector2()
        distance = np.hypot(xs + x0 - self.beaver.x - lean.x, ys + y0 - self.beaver.y - lean.y)
        within = np.hypot(xs + x0 - self.beaver.x, ys + y0 - self.beaver.y) <= reach
        if not within.any():
            return
        # Finish what you started: while the piece being chewed is still in reach, the next bite goes there too,
        # rather than wandering between neighbours and breaking none of them.
        same = within & (window[ys, xs] == self.chewing)
        choice = same if same.any() else within
        nearest = int(np.where(choice, distance, np.inf).argmin())
        section = int(window[ys[nearest], xs[nearest]])
        spot = pygame.Vector2(float(xs[nearest] + x0), float(ys[nearest] + y0))
        self.bite, self.gnaw, self.chewing, self.shown = self.setting("bite_seconds", .3), .16, section, 1.2
        self.strength[section][0] -= 1
        self.sounds.append("chomp")
        self.chips(spot, 6, sprites.BARK_LIGHT)
        if self.strength[section][0] <= 0:
            self.wood[self.sections == section] = False
            self.logs[self.sections == section] = False
            self.sections[self.sections == section] = 0
            self.stale = True
            self.sounds += ["crack", "rush"]
            self.chips(spot, 18, sprites.BARK)
            self.chips(spot, 14, sprites.SKY_LIGHT)

    def chips(self, point, count, color):
        for index in range(count):
            angle, speed = self.rng.uniform(0, math.tau), self.rng.uniform(90, 260) * self.scale
            self.particles.append([point.x, point.y, speed * math.cos(angle), speed * math.sin(angle) - 140 * self.scale,
                                   self.rng.uniform(.3, .6), self.rng.uniform(3, 5) * self.scale, color])

    def finish(self, winner, verdict):
        self.phase, self.winner, self.verdict = "match_over", winner, verdict
        self.sounds.append("fanfare" if winner == self.builder else "splash")

    # ---- drawing ----------------------------------------------------------------------------------------------------

    def sprite(self, key, build):
        if key not in self.art:
            self.art[key] = build()
        return self.art[key]

    def masked(self, texture, mask, outline=None, reach=5):
        """A texture cut to a bool mask, with a line round it."""
        sheet = pygame.Surface(texture.get_size(), pygame.SRCALPHA)
        sheet.blit(texture, (0, 0))
        soft = cv2.GaussianBlur(mask.astype(np.uint8) * 255, (3, 3), 0)
        alpha = pygame.surfarray.pixels_alpha(sheet)
        alpha[:] = soft.T
        del alpha
        if outline is None:
            return sheet
        edge = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))).astype(bool)
        rim = pygame.Surface(texture.get_size(), pygame.SRCALPHA)
        rim.fill(outline)
        alpha = pygame.surfarray.pixels_alpha(rim)
        alpha[:] = (edge.astype(np.uint8) * 255).T
        del alpha
        rim.blit(sheet, (0, 0))
        return rim

    def ground(self):
        unit = self.scale
        surface = sprites.tiled_ground("grass.jpg", self.width, self.height, self.width, lighten=.35)
        cliffs = sprites.tiled_ground("cobble.jpg", self.width, self.height, round(260 * unit))
        surface.blit(self.masked(cliffs, self.solid, sprites.STONE_DARK, 7), (0, 0))
        return surface

    def dam_picture(self):
        """Open strokes are sticks, closed shapes are bark logs; what has been chewed away is simply gone."""
        unit = self.scale
        sheet = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        wood = self.ink & self.allowed if self.wood is None else self.wood
        logs = np.zeros(wood.shape, bool) if self.logs is None else self.logs
        sticks = wood & ~logs
        if sticks.any():
            plain = pygame.Surface((self.width, self.height))
            plain.fill(sprites.BARK)
            sheet.blit(self.masked(plain, sticks, sprites.BARK_LINE, round(5 * unit) | 1), (0, 0))
            core = cv2.erode(sticks.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (round(7 * unit) | 1, round(7 * unit) | 1))).astype(bool)
            if core.any():
                plain.fill(sprites.BARK_LIGHT)
                sheet.blit(self.masked(plain, core), (0, 0))
        if logs.any():
            bark = sprites.tiled_ground("bark_rich.jpg", self.width, self.height, round(190 * unit))
            sheet.blit(self.masked(bark, logs, sprites.BARK_LINE, round(7 * unit) | 1), (0, 0))
        return sheet

    def draw(self, surface):
        unit = self.scale
        frame = self.sprite(("frame",), lambda: pygame.Surface((self.width, self.height), 0, 24))
        frame.blit(self.sprite(("ground",), self.ground), (0, 0))
        # The river: the wet grid, blown up and softened so its edge creeps rather than steps.
        river = self.sprite(("river",), lambda: sprites.tiled_ground("water.jpg", self.width, self.height, round(620 * unit), lighten=.15))
        smooth = cv2.resize(self.wet.astype(np.float32), (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        frame.blit(self.masked(river, (smooth > .5) & ~self.solid), (0, 0))
        if self.stale:
            self.art[("dam",)] = self.dam_picture()
            self.stale = False
        frame.blit(self.art[("dam",)], (0, 0))
        if self.phase == "building":
            # Where wood may not go: round the lodge, and the mouth of the river.
            pygame.draw.circle(frame, sprites.CREAM, self.lodge, self.setting("lodge_clearance", 105) * unit, max(2, round(3 * unit)))
        home = self.sprite(("lodge",), lambda: sprites.lodge(46 * unit))
        frame.blit(home, home.get_rect(center=self.lodge))
        if self.phase in ("attack", "match_over") and self.shown > 0 and self.chewing in self.strength and self.strength[self.chewing][0] > 0:
            left, whole = self.strength[self.chewing]
            bar = pygame.Rect(0, 0, 70 * unit, 12 * unit)
            bar.midbottom = (self.beaver.x, self.beaver.y - 54 * unit)
            pygame.draw.rect(frame, sprites.INK, bar.inflate(6 * unit, 6 * unit), border_radius=round(8 * unit))
            pygame.draw.rect(frame, sprites.WHITE, bar, border_radius=round(6 * unit))
            pygame.draw.rect(frame, sprites.BARK, (bar.x, bar.y, bar.width * left / whole, bar.height), border_radius=round(6 * unit))
        if self.phase != "building":
            head = self.sprite(("tim",), lambda: sprites.tim(19 * unit))
            if self.gnaw > 0:
                head = pygame.transform.smoothscale(head, (round(head.get_width() * 1.12), round(head.get_height() * .88)))
            frame.blit(head, head.get_rect(center=self.beaver + pygame.Vector2(self.facing * 4 * unit * (self.gnaw > 0), 0)))
            short = self.name(self.attacker).replace(" ", "")[:3] if self.names.get(self.attacker) else str(self.attacker)
            word = sprites.lettering(short, max(12, round(19 * unit)), sprites.INK)
            plate = pygame.Rect(0, 0, word.get_width() + 16 * unit, 24 * unit)
            plate.center = self.beaver + pygame.Vector2(0, -36 * unit)
            pygame.draw.rect(frame, sprites.INK, plate.inflate(5 * unit, 5 * unit), border_radius=round(14 * unit))
            pygame.draw.rect(frame, sprites.tint(sprites.PLAYER_COLORS[self.attacker - 1], .45), plate, border_radius=round(12 * unit))
            frame.blit(word, word.get_rect(center=plate.center))
        for x, y, vx, vy, life, size, color in self.particles:
            pygame.draw.circle(frame, sprites.INK, (x, y), size + unit)
            pygame.draw.circle(frame, color, (x, y), size)
        self.draw_hud(frame)
        surface.blit(frame, (0, 0))

    def draw_hud(self, frame):
        unit = self.scale
        bar = pygame.Rect(0, 0, self.width, round(58 * unit))
        pygame.draw.rect(frame, sprites.CREAM, bar)
        pygame.draw.line(frame, sprites.INK, bar.bottomleft, bar.bottomright, max(2, round(3 * unit)))
        title = sprites.sign("DAM IT!", max(14, round(24 * unit)), sprites.BLUE_BRIGHT)
        frame.blit(title, title.get_rect(midleft=(16 * unit, bar.centery)))
        if self.phase == "building":
            message = f"{self.name(self.builder)}: BUILD A DAM TO KEEP THE LODGE DRY   {max(0, math.ceil(self.timer))}"
        elif self.phase == "rising":
            message = "THE RIVER RISES..."
        elif self.phase == "attack":
            message = f"{self.name(self.attacker)}: CHEW THROUGH!   {max(0, math.ceil(self.timer))}"
        else:
            message = ""
        if message:
            line = sprites.sign(message, max(16, round(30 * unit)))
            frame.blit(line, line.get_rect(center=(self.width / 2 - 40 * unit, bar.centery)))
        if self.phase == "building":
            # The rule that matters, shown live: more wood, weaker pieces.
            used = self.wood_length() / self.setting("max_wood", 2600)
            bites = self.bites_per_piece() if self.ink.any() else 0
            lost = 1 - self.total_strength(self.wood_length()) / self.setting("strength", 270)
            grade = "NO WOOD YET" if not bites else f"EACH PIECE: {bites:.0f} BITES" + (f"   TOO MUCH WOOD: DAM {lost:.0%} WEAKER" if lost > .05 else "")
            meter = pygame.Rect(0, 0, 300 * unit, 20 * unit)
            meter.midbottom = (self.width / 2, self.height - 54 * unit)
            pygame.draw.rect(frame, sprites.INK, meter.inflate(8 * unit, 8 * unit), border_radius=round(14 * unit))
            pygame.draw.rect(frame, sprites.WHITE, meter, border_radius=round(10 * unit))
            pygame.draw.rect(frame, sprites.BARK, (meter.x, meter.y, meter.width * min(1, used), meter.height), border_radius=round(10 * unit))
            words = sprites.sign(f"WOOD USED     {grade}", max(14, round(24 * unit)))
            frame.blit(words, words.get_rect(midbottom=(self.width / 2, meter.top - 8 * unit)))
            if not self.camera_ink:
                hint = sprites.sign("Hold the left button to draw wood from bank to bank.  More wood = a weaker dam.  Right click when done.", max(12, round(20 * unit)))
                frame.blit(hint, hint.get_rect(center=(self.width / 2, self.height - 22 * unit)))
        if self.phase in ("attack", "rising"):
            gauge = pygame.Rect(0, 0, 150 * unit, 18 * unit)
            gauge.midtop = (self.lodge.x, self.lodge.y + 62 * unit)
            goal = self.setting("flood_goal", .5)
            pygame.draw.rect(frame, sprites.INK, gauge.inflate(6 * unit, 6 * unit), border_radius=round(12 * unit))
            pygame.draw.rect(frame, sprites.WHITE, gauge, border_radius=round(9 * unit))
            pygame.draw.rect(frame, sprites.SKY, (gauge.x, gauge.y, gauge.width * min(1, self.flooded / goal), gauge.height), border_radius=round(9 * unit))
            words = sprites.sign("FLOODING", max(12, round(18 * unit)))
            frame.blit(words, words.get_rect(midtop=(gauge.centerx, gauge.bottom + 4 * unit)))
        if self.phase == "attack" and not self.camera_ink:
            hint = sprites.sign("The beaver follows the mouse.  Click, or hold, next to the wood to chew it.", max(12, round(22 * unit)))
            frame.blit(hint, hint.get_rect(center=(self.width / 2, self.height - 24 * unit)))
        if self.phase == "match_over":
            veil = pygame.Surface(frame.get_size(), pygame.SRCALPHA)
            veil.fill((*sprites.INK, 90))
            frame.blit(veil, (0, 0))
            plate = pygame.Rect(0, 0, self.width * .66, self.height * .42)
            plate.center = (self.width / 2, self.height * .42)
            pygame.draw.ellipse(frame, sprites.INK, plate.inflate(12 * unit, 12 * unit))
            pygame.draw.ellipse(frame, sprites.CREAM, plate)
            title = sprites.label(f"{self.name(self.winner)} WINS!", max(24, round(116 * unit)), sprites.PLAYER_COLORS[self.winner - 1], tilt=-4)
            if title.get_width() > plate.width * .86:
                title = pygame.transform.rotozoom(title, 0, plate.width * .86 / title.get_width())
            frame.blit(title, title.get_rect(center=(plate.centerx, plate.centery - 26 * unit)))
            line = sprites.lettering(self.verdict, max(14, round(42 * unit)), sprites.BLUE)
            frame.blit(line, line.get_rect(center=(plate.centerx, plate.centery + 78 * unit)))
