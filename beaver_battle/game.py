"""Fixed-step canoe combat, independent of cameras and controller transport."""

from dataclasses import dataclass, field
import math
from pathlib import Path
import random

import cv2
import numpy as np
import pygame

from beaver_battle import sprites
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
    bounce_heading: float = 0.0


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


def closed_shapes(walls, gap=5, min_area=400, max_area=math.inf):
    """Find bright regions fully enclosed by ink. Returns (ink plus interiors, shapes).

    Small breaks in an outline are bridged only for the enclosure test. Regions
    touching the board edge are open water, and enclosures above max_area stay
    hollow so an arena outline cannot turn the whole board solid.
    """
    ink = walls.astype(np.uint8)
    if gap > 1:
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap, gap)))
    contours, hierarchy = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    interior = np.zeros_like(ink)
    shapes = []
    for contour, links in zip(contours, hierarchy[0] if hierarchy is not None else []):
        if links[3] < 0 or not min_area <= cv2.contourArea(contour) <= max_area:
            continue
        center, (across, along), degrees = cv2.minAreaRect(contour)
        if across > along:
            across, along, degrees = along, across, degrees - 90
        ratio = along / max(across, 1)
        # minAreaRect's angle belongs to its first side; the grain follows the long side.
        shapes.append(Shape("log" if ratio >= 2 else "rock", contour, center,
                            math.radians(degrees + 90) % math.pi, ratio))
        cv2.drawContours(interior, [contour], -1, 1, -1)
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
    backdrop: pygame.Surface | None = None

    def setting(self, name, default):
        return self.config.get("game", {}).get(name, default)

    def new_match(self, player_ids, walls=None):
        ids = list(player_ids)
        if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids) or any(player not in (1, 2, 3) for player in ids):
            raise ValueError("Choose one to three distinct controller IDs from 1, 2, 3")
        self.width = int(self.setting("width", 1280))
        self.height = int(self.setting("height", 720))
        self.scale = min(self.width / 1280, self.height / 720)
        self.shore = 0
        self.art.clear()
        self.rng = random.Random(self.setting("seed", 2026))
        self.scores = dict.fromkeys(ids, 0)
        self.drop_bag = []
        self.wall_source = None
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
            ("wall", .32, .30, 58, 3, (116, 22)),
            ("wall", .70, .73, 58, 3, (116, 22)),
            ("barrier", .50, .53, 48, 3, (22, 96)),
            ("barrier", .40, .12, 45, 3, (90, 20)),
            ("barrier", .42, .66, 45, 3, (90, 20)),
            ("barrier", .86, .72, 40, 3, (20, 80)),
            ("barrier", .15, .45, 40, 3, (20, 80)),
            ("asteroid", .28, .68, 22, 2, None),
            ("asteroid", .72, .28, 22, 2, None),
            ("lilypad", .23, .43, 16, 1, None),
            ("lilypad", .77, .57, 16, 1, None),
            ("lilypad", .50, .26, 16, 1, None),
            ("lilypad", .12, .66, 16, 1, None),
            ("lilypad", .88, .40, 16, 1, None),
            ("lilypad", .62, .86, 16, 1, None),
        ]
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
        if walls is not None:
            walls, shapes = closed_shapes(walls, round(self.setting("shape_gap", 5) * self.scale),
                                          self.setting("shape_min_area", 400) * self.scale ** 2,
                                          self.setting("shape_max_fraction", .25) * self.width * self.height)
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
        self.walls = walls
        self.wall_distance = cv2.distanceTransform((~walls).astype(np.uint8), cv2.DIST_L2, 5) if walls is not None else None
        self.wall_masks.clear()
        return True

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
                fraction = circle_hit(start, end, target.pos, radius + target.radius)
            if fraction is not None and 0 <= fraction <= closest:
                closest, hit = fraction, target
        return start + delta * closest, hit

    def hit(self, target, damage=1):
        if isinstance(target, Player):
            if target.state == "eliminated" or target.invulnerability > 0:
                return False
            target.state = "beaver" if target.state == "canoe" else "eliminated"
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
            if target.hp <= 0 and target.kind in ("barrel", "lilypad", "asteroid"):
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
        start = pos + forward * (radius + 6 * self.scale)
        end, blocker = self.trace(pos, start, 4 * self.scale, owner=owner, ignore=ignore)
        if blocker is not None:
            self.hit(blocker)
        else:
            self.rocks.append(Rock(owner, start, forward * self.setting("rock_speed", 650) * self.scale,
                                   4 * self.scale, self.setting("rock_lifetime", 2)))

    def activate(self, player):
        kind = player.powerup
        if not kind or player.reload > 0 or player.state != "canoe":
            return
        if kind == "laser":
            end, target = self.trace(player.pos, player.pos + direction(player.heading) * self.width * 2,
                                     2 * self.scale, owner=player.player_id)
            self.hit(target, 3)
            self.effects.append(Effect("laser", player.pos.copy(), end, .15))
        elif kind == "jouster":
            player.joust = 2.0
        elif kind == "mine":
            pos = self.nearest_free(player.pos - direction(player.heading) * 32 * self.scale, 9 * self.scale)
            if pos is None:
                return
            self.mines.append(Mine(player.player_id, pos, 9 * self.scale))
        player.powerup = None

    def explode(self, mine):
        mine.active = False
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
                self.hit(target, 3)

    def update(self, dt, inputs, walls=None):
        if not math.isfinite(dt) or not 0 <= dt <= 10:
            raise ValueError("Game dt must be finite and between zero and ten seconds")
        self.events = []
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
            if player.reload > 0:
                player.reload = countdown(player.reload, dt)
                if player.reload == 0:
                    player.ammo = self.setting("magazine", 3)
            if player.bounce > 0:
                player.bounce = countdown(player.bounce, dt)
                player.heading = turn_toward(player.heading, player.bounce_heading, math.radians(720) * dt)
            elif control.connected and control.aim is not None and control.aim_age <= self.config.get("camera", {}).get("stale_seconds", .5):
                if all(math.isfinite(value) for value in control.aim):
                    delta = pygame.Vector2(control.aim) - player.pos
                    if delta.length() > self.setting("aim_deadzone", 24) * self.scale:
                        player.heading = turn_toward(player.heading, math.atan2(delta.y, delta.x),
                                                     math.radians(self.setting("turn_speed", 240)) * dt)
            fire = control.fire and control.connected
            if player.state == "beaver":
                target_speed = self.setting("beaver_boost_speed", 150) if fire else self.setting("beaver_speed", 75)
                change = 300 * self.scale * dt
                player.speed += max(-change, min(change, target_speed * self.scale - player.speed))
            else:
                player.speed = self.setting("canoe_speed", 240) * self.scale
            velocity = direction(player.heading) * player.speed * (1.5 if player.joust else 1)
            rebound = self.move(player, velocity, dt)
            if rebound != velocity and rebound.length_squared():
                # Glance off smoothly: slide along the obstacle while the bow swings round to the rebound heading.
                player.bounce = .3
                player.bounce_heading = math.atan2(rebound.y, rebound.x)
            if fire and player.state == "canoe" and player.reload == 0 and player.cooldown == 0:
                self.shoot(player.player_id, player.pos, player.heading, player.radius)
                player.ammo -= 1
                player.cooldown = self.setting("shot_interval", .30)
                if player.ammo <= 0:
                    player.reload = self.setting("reload_seconds", 2.5)
            special = control.special and control.connected
            if special and not player.special_held:
                self.activate(player)
            player.special_held = special
            if player.joust:
                end, target = self.trace(player.pos, player.pos + direction(player.heading) * 54 * self.scale,
                                         7 * self.scale, owner=player.player_id)
                if target is not None and target != "wall":
                    self.hit(target, 3)
                    player.joust = 0
        for prop in self.props:
            if prop.hp > 0 and prop.velocity.length_squared():
                prop.velocity = self.move(prop, prop.velocity, dt)
        self.update_rocks(dt)
        self.update_hazards(dt)
        self.update_pickups(dt)
        self.update_mines(dt)
        survivors = [player.player_id for player in self.players.values() if player.state != "eliminated"]
        if len(self.players) > 1 and len(survivors) <= 1:
            self.winner = survivors[0] if survivors else None
            if self.winner is not None:
                self.scores[self.winner] += 1
            self.phase = "match_over" if self.winner is not None and self.scores[self.winner] >= self.setting("winning_score", 5) else "round_over"
            self.round_timer = self.setting("round_delay", 3)

    def update_rocks(self, dt):
        remaining = []
        for rock in self.rocks:
            end, target = self.trace(rock.pos, rock.pos + rock.velocity * dt, rock.radius, owner=rock.owner)
            rock.pos = end
            rock.lifetime -= dt
            if target is not None:
                self.hit(target)
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
            pickup.velocity = self.move(pickup, pickup.velocity, dt)
            collected = False
            for player in self.players.values():
                if player.state == "canoe" and player.powerup is None and player.pos.distance_to(pickup.pos) <= player.radius + pickup.radius:
                    player.powerup = pickup.kind
                    collected = True
                    break
            if not collected and pickup.lifetime > 0:
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

    def text(self, surface, message, position, size=20, color=INK, centered=False):
        size = max(12, round(size * self.scale))
        if size not in self.fonts:
            if not pygame.font.get_init():
                pygame.font.init()
            self.fonts[size] = pygame.font.Font(None, size)
        image = self.fonts[size].render(str(message), True, color)
        rect = image.get_rect(center=position) if centered else image.get_rect(topleft=position)
        surface.blit(image, rect)

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
        x, y, width, height = cv2.boundingRect(shape.contour)
        mask = np.zeros((height, width), np.uint8)
        cv2.drawContours(mask, [shape.contour], -1, 255, -1, offset=(-x, -y))
        return sprites.outline_fill(shape.kind, cv2.GaussianBlur(mask, (3, 3), 0), (x, y), shape.angle, self.scale), (x, y)

    def draw(self, surface):
        surface.fill(WATER)
        if not self.players:
            return
        unit = self.scale
        if self.shape_art is None:
            self.shape_art = [self.shape_image(shape) for shape in self.shapes]
        surface.blits(self.shape_art)
        if self.backdrop is not None:
            surface.blit(self.backdrop, (0, 0))
        for index, prop in enumerate(self.props):
            if prop.hp <= 0:
                continue
            pos, radius = prop.pos, prop.radius
            if prop.size:
                self.stamp(surface, self.sprite(("log", index), lambda: sprites.log(prop.size[0] / unit, prop.size[1] / unit, unit, seed=index)), pos)
            elif prop.kind == "asteroid":
                self.stamp(surface, self.sprite(("boulder", index), lambda: sprites.boulder(radius, index)), pos)
            elif prop.kind == "lilypad":
                self.stamp(surface, self.sprite(("lilypad", index), lambda: sprites.lily_pad(radius, degrees=index * 67)), pos)
                continue
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
        for pickup in self.pickups:
            bob = 1 + .06 * math.sin(self.time * 4 + pickup.pos.x)
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
            elif player.state == "canoe":
                if player.joust:
                    self.stamp(surface, self.sprite(("horn",), lambda: sprites.horn(54 * unit)), player.pos + forward * (player.radius * 2.2 + 20 * unit), player.heading)
                self.stamp(surface, self.sprite(("canoe", color, player.radius), lambda: sprites.canoe(player.radius, color)), player.pos, player.heading)
                self.stamp(surface, self.sprite(("tim", player.radius), lambda: sprites.tim(player.radius * 1.05)), player.pos - forward * 2 * unit)
            else:
                self.stamp(surface, self.sprite(("swimmer", color, player.radius), lambda: sprites.swimmer(player.radius, color)), player.pos, player.heading)
                self.stamp(surface, self.sprite(("tim", player.radius), lambda: sprites.tim(player.radius * 1.45, paws=False)), player.pos)
            if player.invulnerability > 0:
                pygame.draw.circle(surface, sprites.CREAM, player.pos, player.radius * 2.5, max(1, round(3 * unit)))
            tag = player.pos + pygame.Vector2(0, -player.radius * 2 - 14 * unit)
            pygame.draw.circle(surface, INK, tag, 11 * unit)
            pygame.draw.circle(surface, sprites.tint(color, .45), tag, 8.5 * unit)
            self.text(surface, str(player.player_id), tag, 19, centered=True)
            if player.powerup:
                self.stamp(surface, self.sprite(("held", player.powerup), lambda: sprites.pickup(9 * unit, player.powerup)), tag + pygame.Vector2(24 * unit, 0))
            if player.state == "canoe":
                for rock in range(self.setting("magazine", 3)):
                    center = player.pos + pygame.Vector2((rock - (self.setting("magazine", 3) - 1) / 2) * 10 * unit, player.radius * 2 + 8 * unit)
                    pygame.draw.circle(surface, INK, center, 4 * unit)
                    pygame.draw.circle(surface, sprites.STONE if rock < player.ammo and not player.reload else sprites.WHITE, center, 2.6 * unit)
        for effect in self.effects:
            if effect.kind == "laser":
                pygame.draw.line(surface, INK, effect.start, effect.end, max(3, round(12 * unit)))
                pygame.draw.line(surface, sprites.SKY_LIGHT, effect.start, effect.end, max(2, round(7 * unit)))
                pygame.draw.line(surface, sprites.WHITE, effect.start, effect.end, max(1, round(3 * unit)))
            else:
                radius = effect.end.x * (1 - effect.lifetime / (.35 if effect.kind == "splash" else .3))
                pygame.draw.circle(surface, sprites.WHITE if effect.kind == "splash" else sprites.BUTTER, effect.start,
                                   max(1, round(radius)), max(1, round(5 * unit)))
        goal = self.setting("winning_score", 5)
        group = (46 + goal * 22) * unit
        left = self.width / 2 - (len(self.players) * group + (len(self.players) - 1) * 34 * unit) / 2
        for index, player in enumerate(self.players.values()):
            color = COLORS[player.player_id - 1]
            x, y = left + index * (group + 34 * unit), 28 * unit
            pygame.draw.circle(surface, INK, (x + 15 * unit, y), 15 * unit)
            pygame.draw.circle(surface, sprites.tint(color, .35), (x + 15 * unit, y), 12 * unit)
            self.text(surface, str(player.player_id), (x + 15 * unit, y), 24, centered=True)
            for point in range(goal):
                center = (x + (50 + point * 22) * unit, y)
                pygame.draw.circle(surface, INK, center, 9 * unit)
                pygame.draw.circle(surface, color if point < self.scores[player.player_id] else sprites.WHITE, center, 6.5 * unit)
        if self.blocked or self.phase != "playing":
            box = pygame.FRect(self.width * .15, self.height * .38, self.width * .7, self.height * .22)
            pygame.draw.rect(surface, sprites.CREAM, box, border_radius=max(1, round(22 * unit)))
            pygame.draw.rect(surface, INK, box, max(1, round(4 * unit)), border_radius=max(1, round(22 * unit)))
            message = self.error if self.blocked else (f"PLAYER {self.winner} WINS!" if self.winner is not None else "DRAW")
            self.text(surface, message, (self.width / 2, self.height * .45), 44, centered=True)
            subtitle = "Erase or move a physical obstacle" if self.blocked else (
                "First to five! Return to lobby to play again" if self.phase == "match_over" else f"Next round in {max(1, math.ceil(self.round_timer))}")
            self.text(surface, subtitle, (self.width / 2, self.height * .53), 23, centered=True)
