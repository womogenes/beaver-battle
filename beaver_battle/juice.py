"""Game feel: everything here is cosmetic and runs on the wall clock, so hit-stop can freeze the
simulation while bursts, shake and banners keep moving. game.py reports what happened in game.fx.

Colours stay pastel: a dark flash would read as a wall to the camera and a red one as a laser.
"""

from dataclasses import dataclass, field
import math
import random
import time

import pygame

from beaver_battle import sprites


@dataclass
class Body:
    """Per-player animation state that the simulation never sees."""
    pos: pygame.Vector2
    heading: float
    turn: float = 0.0
    lean: float = 0.0
    squash: float = 0.0
    spin: float = 0.0
    speed: float = 0.0
    trail: float = 0.0


@dataclass
class Juice:
    scale: float
    rng: random.Random = field(default_factory=lambda: random.Random(5))
    clock: float | None = None
    age: float = 0.0
    particles: list = field(default_factory=list)
    bursts: list = field(default_factory=list)
    rings: list = field(default_factory=list)
    ghosts: list = field(default_factory=list)
    scores: list = field(default_factory=list)
    bodies: dict = field(default_factory=dict)
    pops: dict = field(default_factory=dict)
    shake: float = 0.0
    flash: float = 0.0
    flash_color: tuple = sprites.WHITE
    zoom_age: float | None = None
    focus: pygame.Vector2 | None = None
    banner: tuple | None = None
    banner_age: float = 0.0

    def color(self, player_id):
        return sprites.PLAYER_COLORS[player_id - 1] if player_id and 1 <= player_id <= len(sprites.PLAYER_COLORS) else sprites.BUTTER

    def spray(self, pos, count, speed, color, size, life, heading=None, spread=math.pi, kind="dot"):
        for index in range(count):
            angle = self.rng.uniform(0, math.tau) if heading is None else heading + self.rng.uniform(-spread, spread)
            pace = speed * self.rng.uniform(.35, 1) * self.scale
            self.particles.append([pos[0], pos[1], pace * math.cos(angle), pace * math.sin(angle), life * self.rng.uniform(.6, 1),
                                   life, size * self.rng.uniform(.6, 1.2) * self.scale, color, kind])

    def absorb(self, game):
        """Turn the simulation's reports into bursts, shake, ghosts and banners."""
        for event in game.fx:
            kind, pos = event[0], event[1]
            if kind in ("hit", "sink"):
                push, victim, attacker, final = event[2], event[3], event[4], event[5]
                color, heading = self.color(attacker), math.atan2(push.y, push.x) if push.length_squared() else None
                sink = kind == "sink"
                self.bursts.append([pos.copy(), 0.0, .34 if sink else .26, (115 if sink else 78) * self.scale, color, self.rng.uniform(0, math.tau)])
                self.rings.append([pos.copy(), 0.0, .4, (150 if sink else 95) * self.scale, color])
                self.spray(pos, 22 if sink else 14, 620, color, 8, .55, heading, .8)
                self.spray(pos, 16 if sink else 9, 420, sprites.SKY, 5, .5, heading, 1.6, "drop")
                self.spray(pos, 8, 300, sprites.WHITE, 6, .4, kind="star")
                self.shake = max(self.shake, (22 if sink else 13) * self.scale)
                self.flash, self.flash_color = (.22 if sink else .1), color
                body = self.bodies.get(victim)
                if body:
                    body.spin, body.squash = 1.0, 1.0
                if sink:
                    launch = (push if push.length_squared() else pygame.Vector2(0, -1)) * 760 * self.scale + pygame.Vector2(0, -260 * self.scale)
                    self.ghosts.append([pos.copy(), launch, 0.0, 1.1, victim, self.rng.choice((-1, 1))])
                if final:
                    self.zoom_age, self.focus = 0.0, pos.copy()
            elif kind == "round":
                winner, match = event[2], event[3]
                self.banner = (f"{game.name(winner)}!", self.color(winner), match)
                self.banner_age = -.45
            elif kind == "pickup":
                player_id = event[2]
                color = self.color(player_id)
                self.rings.append([pos.copy(), 0.0, .35, 70 * self.scale, color])
                self.rings.append([pos.copy(), -.06, .35, 46 * self.scale, sprites.WHITE])
                self.spray(pos, 14, 380, color, 6, .45, kind="star")
                self.spray(pos, 8, 260, sprites.BUTTER, 5, .4)
                self.pops[player_id] = 1.0
            elif kind == "score":
                self.scores.append([pos.copy(), 0.0, event[2]])
            elif kind == "return":
                color = self.color(event[2])
                self.rings.append([pos.copy(), 0.0, .45, 90 * self.scale, color])
                self.spray(pos, 16, 360, color, 6, .5, kind="star")
                self.spray(pos, 10, 300, sprites.SKY, 5, .45, kind="drop")
            elif kind == "shoot":
                body = self.bodies.get(event[2])
                if body:
                    body.squash = max(body.squash, .55)
                self.spray(pos, 5, 210, sprites.SKY, 4, .25, event[3], .7, "drop")
            elif kind == "chip":
                self.spray(pos, 7, 300, sprites.STONE, 5, .35)
                self.spray(pos, 4, 200, sprites.SKY, 4, .3, kind="drop")
                self.shake = max(self.shake, 3 * self.scale)
            elif kind == "boom":
                self.bursts.append([pos.copy(), 0.0, .4, 150 * self.scale, sprites.BUTTER, 0.0])
                self.rings.append([pos.copy(), 0.0, .45, 190 * self.scale, sprites.LAVENDER])
                self.spray(pos, 30, 700, sprites.LAVENDER, 8, .6)
                self.spray(pos, 18, 500, sprites.SKY, 5, .55, kind="drop")
                self.shake = max(self.shake, 18 * self.scale)
            elif kind == "bump":
                self.spray(pos, 6, 230, sprites.SKY, 4, .3, kind="drop")
                self.shake = max(self.shake, 2.5 * self.scale)
            elif kind == "reset":
                self.ghosts.clear()
                self.banner = None
        game.fx.clear()

    def step(self, game):
        now = time.perf_counter()
        dt = 0.0 if self.clock is None else min(.05, max(0.0, now - self.clock))
        self.clock = now
        self.age += dt
        self.absorb(game)
        for player in game.players.values():
            body = self.bodies.setdefault(player.player_id, Body(player.pos.copy(), player.heading))
            if player.state == "eliminated":
                continue
            moved = player.pos.distance_to(body.pos)
            if dt > 0 and game.freeze <= 0:
                turn = ((player.heading - body.heading + math.pi) % math.tau - math.pi) / dt
                body.turn += (turn - body.turn) * min(1, 12 * dt)
                body.lean += (max(-1, min(1, body.turn / math.radians(260))) - body.lean) * min(1, 9 * dt)
                pace = moved / dt
                if player.state == "beaver":
                    body.squash = max(body.squash, min(.5, abs(pace - body.speed) / (220 * self.scale)))
                body.speed = pace
                back = player.pos - pygame.Vector2(math.cos(player.heading), math.sin(player.heading)) * player.radius * (1.9 if player.state == "canoe" else 1.2)
                side = pygame.Vector2(-math.sin(player.heading), math.cos(player.heading))
                body.trail += moved
                if body.trail > 7 * self.scale:
                    # A wake: two lines of foam peeling away from the stern.
                    body.trail = 0
                    for way in (-1, 1):
                        origin = back + side * way * player.radius * .55
                        drift = side * way * 26 * self.scale
                        self.particles.append([origin.x, origin.y, drift.x, drift.y, .62, .62, 4.4 * self.scale, sprites.SKY_LIGHT, "wake"])
                if player.state == "canoe" and abs(body.turn) > math.radians(165):
                    # A hard turn digs the stern in and throws water off the outside.
                    out = -1 if body.turn > 0 else 1
                    self.spray(back + side * out * player.radius * .6, 2, 330, sprites.SKY, 4.5, .4,
                               math.atan2(side.y * out, side.x * out), .5, "drop")
            body.pos, body.heading = player.pos.copy(), player.heading
            body.squash = max(0.0, body.squash - 5 * dt)
            body.spin = max(0.0, body.spin - 2.2 * dt)
        if game.phase == "match_over" and self.banner and self.banner_age > .3 and self.rng.random() < .7:
            color = self.rng.choice(sprites.PLAYER_COLORS + [sprites.PINK, sprites.BUTTER, sprites.MINT])
            self.particles.append([self.rng.uniform(0, game.width), -10, self.rng.uniform(-40, 40) * self.scale, self.rng.uniform(140, 260) * self.scale,
                                   4.0, 4.0, 9 * self.scale, color, "confetti"])
        for particle in self.particles:
            particle[0] += particle[2] * dt
            particle[1] += particle[3] * dt
            if particle[8] not in ("wake", "confetti"):
                particle[2] *= .02 ** dt
                particle[3] *= .02 ** dt
            particle[4] -= dt
        self.particles = [particle for particle in self.particles if particle[4] > 0][-700:]
        for group in (self.bursts, self.rings):
            for item in group:
                item[1] += dt
        self.bursts = [burst for burst in self.bursts if burst[1] < burst[2]]
        self.rings = [ring for ring in self.rings if ring[1] < ring[2]]
        for ghost in self.ghosts:
            ghost[0] += ghost[1] * dt
            ghost[1].y += 900 * self.scale * dt
            ghost[2] += dt
        self.ghosts = [ghost for ghost in self.ghosts if ghost[2] < ghost[3]]
        for score in self.scores:
            score[1] += dt
        self.scores = [score for score in self.scores if score[1] < 1.1]
        for player_id in list(self.pops):
            self.pops[player_id] = max(0.0, self.pops[player_id] - 3.2 * dt)
        self.shake *= .0006 ** dt
        self.flash = max(0.0, self.flash - dt)
        self.banner_age += dt
        if self.zoom_age is not None:
            self.zoom_age += dt
            if self.zoom_age > 1.0:
                self.zoom_age = None

    def body(self, player_id):
        return self.bodies.get(player_id)

    def draw_under(self, surface):
        for x, y, vx, vy, life, full, size, color, kind in self.particles:
            if kind == "wake":
                fade = life / full
                pygame.draw.circle(surface, sprites.tint(color, 1 - fade), (x, y), max(1, size * (.5 + .5 * fade)))

    def draw_over(self, surface):
        ink = max(1, round(2 * self.scale))
        for pos, age, span, reach, color in self.rings:
            if age < 0:
                continue
            grow = 1 - (1 - age / span) ** 3
            if (9 * self.scale) * (1 - age / span) < 1.5:
                continue  # Let a spent ring vanish instead of lingering as a hairline circle.
            pygame.draw.circle(surface, sprites.INK, pos, reach * grow + ink, max(1, round((9 * self.scale) * (1 - age / span)) + 2 * ink))
            pygame.draw.circle(surface, color, pos, reach * grow, max(1, round((9 * self.scale) * (1 - age / span))))
        for pos, age, span, reach, color, twist in self.bursts:
            # A spiky comic-book star that slams out in a few frames and then collapses.
            grow = min(1, age / .05) * (1 - max(0, (age - .1) / (span - .1)) ** 2)
            for fill, size in ((sprites.INK, reach * grow + 3 * ink), (color, reach * grow), (sprites.WHITE, reach * grow * .55)):
                points = [(pos.x + size * (1 if index % 2 else .52) * math.cos(twist + index * math.tau / 20),
                           pos.y + size * (1 if index % 2 else .52) * math.sin(twist + index * math.tau / 20)) for index in range(20)]
                pygame.draw.polygon(surface, fill, points)
        for x, y, vx, vy, life, full, size, color, kind in self.particles:
            fade = life / full
            if kind == "wake":
                continue
            if kind == "confetti":
                wide = size * abs(math.sin(life * 9 + x))
                pygame.draw.rect(surface, color, (x - wide / 2, y - size / 2, max(1, wide), size))
            elif kind == "star":
                reach = size * fade * 1.6
                pygame.draw.polygon(surface, color, [(x, y - reach), (x + reach * .3, y - reach * .3), (x + reach, y), (x + reach * .3, y + reach * .3),
                                                     (x, y + reach), (x - reach * .3, y + reach * .3), (x - reach, y), (x - reach * .3, y - reach * .3)])
                pygame.draw.polygon(surface, sprites.INK, [(x, y - reach), (x + reach * .3, y - reach * .3), (x + reach, y), (x + reach * .3, y + reach * .3),
                                                           (x, y + reach), (x - reach * .3, y + reach * .3), (x - reach, y), (x - reach * .3, y - reach * .3)], 1)
            else:
                radius = max(1, size * fade)
                if radius > 2.5:
                    pygame.draw.circle(surface, sprites.INK, (x, y), radius + ink)
                pygame.draw.circle(surface, color, (x, y), radius)

    def draw_scores(self, surface):
        """+1 pops out of a sinking in the scorer's colour and floats up."""
        for pos, age, player_id in self.scores:
            grow = 1 + 1.4 * max(0, 1 - age / .14) ** 2
            image = sprites.label("+1", max(14, round(54 * self.scale)), self.color(player_id), tilt=-6)
            if grow > 1.01:
                image = pygame.transform.rotozoom(image, 0, grow)
            surface.blit(image, image.get_rect(center=(pos.x, pos.y - (30 + 70 * age) * self.scale)))

    def draw_ghosts(self, surface, portrait):
        """A sunk beaver is flung off the board, spinning and looming toward the audience."""
        for pos, velocity, age, span, player_id, way in self.ghosts:
            image = pygame.transform.rotozoom(portrait(player_id), way * age * 1100, 1 + 1.6 * age / span)
            surface.blit(image, image.get_rect(center=pos))

    def present(self, canvas, screen):
        """Blit the world with shake, the killing-blow zoom, and a coloured flash."""
        offset = pygame.Vector2(self.rng.uniform(-1, 1), self.rng.uniform(-1, 1)) * self.shake if self.shake > .4 else pygame.Vector2()
        zoom = 1.0
        if self.zoom_age is not None and self.focus is not None:
            rise, fall = min(1, self.zoom_age / .12), max(0, (self.zoom_age - .55) / .45)
            zoom = 1 + .3 * (1 - (1 - rise) ** 2) * (1 - fall * fall)
        if zoom > 1.005:
            width, height = canvas.get_size()
            scaled = pygame.transform.smoothscale(canvas, (round(width * zoom), round(height * zoom)))
            corner = pygame.Vector2(self.focus.x * (1 - zoom), self.focus.y * (1 - zoom))
            corner.x = min(0, max(width - scaled.get_width(), corner.x))
            corner.y = min(0, max(height - scaled.get_height(), corner.y))
            screen.blit(scaled, corner + offset)
        else:
            screen.blit(canvas, offset)
        if self.flash > 0:
            wash = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            wash.fill((*sprites.tint(self.flash_color, .25), round(150 * min(1, self.flash / .12))))
            screen.blit(wash, (0, 0))

    def draw_banner(self, surface, game):
        """PLAYER 1! slammed onto the board once the knockout has had its moment."""
        if self.banner is None or self.banner_age < 0:
            return
        message, color, match = self.banner
        unit, age = self.scale, self.banner_age
        slam = 1 + 2.2 * max(0, 1 - age / .16) ** 2 + .1 * math.sin(min(1, age / .4) * math.pi) * (age < .4) + .025 * math.sin(age * 7) * (age >= .4)
        # Set the name apart from the river: hush the board under a soft veil and put a cream plate,
        # rimmed in the outline colour, behind the words.
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((*sprites.INK, round(95 * min(1, age / .2))))
        surface.blit(veil, (0, 0))
        middle, reach = pygame.Vector2(game.width / 2, game.height * .44), game.width * .4 * min(1, age / .25)
        plate = pygame.Rect(middle.x - reach * .8, middle.y - reach * .31, reach * 1.6, reach * .69)
        pygame.draw.ellipse(surface, sprites.INK, plate.inflate(12 * unit, 12 * unit))
        pygame.draw.ellipse(surface, sprites.CREAM, plate)
        title = sprites.label(message, max(24, round(176 * unit)), color, tilt=-4)
        # A long name is shrunk to fit the plate rather than spilling off it.
        slam *= min(1, game.width * .6 / title.get_width())
        if abs(slam - 1) > .004:
            title = pygame.transform.rotozoom(title, 0, slam)
        center = (game.width / 2, game.height * .44 + 5 * unit * math.sin(age * 3))
        surface.blit(title, title.get_rect(center=center))
        if age > .3:
            text = "WINS THE MATCH!" if match else f"NEXT ROUND IN {max(1, math.ceil(game.round_timer))}"
            if message == "DRAW!":
                text = "NOBODY WINS THAT ONE"
            # Solid dark letters on the cream plate: white-on-cream was hard to read from across a room.
            line = sprites.lettering(text, max(14, round((60 if match else 46) * unit)), sprites.INK)
            surface.blit(line, line.get_rect(center=(game.width / 2, game.height * .44 + 140 * unit)))
