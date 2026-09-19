"""Fixed-step canoe combat, independent of cameras and controller transport."""

from dataclasses import dataclass, field
import math
from pathlib import Path
import random

import cv2
import numpy as np
import pygame

from beaver_battle.model import FeedbackEvent, PlayerInput


COLORS = [(110, 200, 235), (235, 195, 120), (195, 165, 235)]
INK = (85, 120, 135)
WATER = (225, 245, 235)


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

    def setting(self, name, default):
        return self.config.get("game", {}).get(name, default)

    def new_match(self, player_ids, walls=None):
        ids = list(player_ids)
        if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids) or any(player not in (1, 2, 3) for player in ids):
            raise ValueError("Choose one to three distinct controller IDs from 1, 2, 3")
        self.width = int(self.setting("width", 1280))
        self.height = int(self.setting("height", 720))
        self.scale = min(self.width / 1280, self.height / 720)
        self.rng = random.Random(self.setting("seed", 2026))
        self.scores = dict.fromkeys(ids, 0)
        self.drop_bag = []
        self.wall_source = None
        self.walls = None
        self.wall_distance = None
        self.wall_masks.clear()
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
            ("asteroid", .28, .68, 29, 2, None),
            ("asteroid", .72, .28, 29, 2, None),
            ("barrel", .23, .43, 19, 1, None),
            ("barrel", .77, .57, 19, 1, None),
            ("barrel", .50, .28, 19, 1, None),
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
        if not radius <= pos.x < self.width - radius or not radius <= pos.y < self.height - radius:
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
        margin = max(1, math.ceil(radius))
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
        outside = (xs < radius) | (ys < radius) | (xs >= self.width - radius) | (ys >= self.height - radius)
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
            if control.connected and control.aim is not None and control.aim_age <= self.config.get("camera", {}).get("stale_seconds", .5):
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
            velocity = self.move(player, velocity, dt)
            if velocity.length_squared():
                player.heading = math.atan2(velocity.y, velocity.x)
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

    def draw(self, surface):
        surface.fill(WATER)
        if not self.players:
            return
        unit = self.scale
        for y in range(round(80 * unit), self.height, max(1, round(60 * unit))):
            pygame.draw.line(surface, (210, 235, 230), (0, y), (self.width, y), 1)
        pygame.draw.rect(surface, (125, 185, 190), surface.get_rect(), max(1, round(4 * unit)))
        for prop in self.props:
            if prop.hp <= 0:
                continue
            pos, radius = prop.pos, prop.radius
            if prop.size:
                pygame.draw.rect(surface, (170, 205, 220) if prop.kind == "wall" else (220, 210, 165), prop_rect(prop), border_radius=max(1, round(5 * unit)))
                pygame.draw.rect(surface, INK, prop_rect(prop), max(1, round(2 * unit)), border_radius=max(1, round(5 * unit)))
            elif prop.kind == "asteroid":
                points = [pos + direction(index * math.tau / 7) * radius * (1 if index % 2 else .85) for index in range(7)]
                pygame.draw.polygon(surface, (170, 190, 195), points)
                pygame.draw.polygon(surface, INK, points, max(1, round(2 * unit)))
            elif prop.kind == "barrel":
                pygame.draw.circle(surface, (225, 200, 140), pos, radius)
                pygame.draw.circle(surface, (155, 155, 110), pos, radius, max(1, round(3 * unit)))
                self.text(surface, "?", pos, 25, centered=True)
            else:
                pygame.draw.circle(surface, (190, 175, 230) if prop.kind == "beam" else (165, 205, 195), pos, radius)
                pygame.draw.circle(surface, INK, pos, radius, max(1, round(2 * unit)))
                pygame.draw.line(surface, INK, pos, pos + direction(prop.heading) * (radius + 11 * unit), max(2, round(7 * unit)))
            for index in range(prop.hp):
                pygame.draw.circle(surface, INK, (pos.x + (index - (prop.hp - 1) / 2) * 7 * unit, pos.y + radius + 7 * unit), max(1, round(2 * unit)))
            if prop.kind == "beam" and prop.beam_end is not None:
                active = self.time % 5 >= 1
                pygame.draw.line(surface, (210, 140, 220) if active else (200, 185, 215), pos, prop.beam_end, max(1, round((8 if active else 2) * unit)))
        for pickup in self.pickups:
            pygame.draw.circle(surface, (245, 235, 155), pickup.pos, pickup.radius + 4 * unit)
            self.text(surface, {"laser": "L", "jouster": "J", "mine": "M"}[pickup.kind], pickup.pos, 24, centered=True)
        for mine in self.mines:
            pygame.draw.circle(surface, (185, 170, 220), mine.pos, mine.radius)
            pygame.draw.circle(surface, INK, mine.pos, mine.radius, max(1, round(2 * unit)))
            if mine.age >= .6:
                pygame.draw.circle(surface, (250, 240, 170), mine.pos, max(2, round(3 * unit)))
        for rock in self.rocks:
            pygame.draw.circle(surface, (110, 155, 165), rock.pos, rock.radius)
        for player in self.players.values():
            if player.state == "eliminated":
                continue
            color = COLORS[player.player_id - 1]
            forward, side = direction(player.heading), direction(player.heading + math.pi / 2)
            sprite = self.sprites.get(player.state)
            if sprite:
                image = pygame.transform.smoothscale(sprite, (max(1, round(player.radius * 3)), max(1, round(player.radius * 2))))
                image = pygame.transform.rotate(image, -math.degrees(player.heading))
                surface.blit(image, image.get_rect(center=player.pos))
            elif player.state == "canoe":
                points = [player.pos + forward * player.radius * 1.5,
                          player.pos - forward * player.radius + side * player.radius,
                          player.pos - forward * player.radius * 1.5,
                          player.pos - forward * player.radius - side * player.radius]
                pygame.draw.polygon(surface, color, points)
                pygame.draw.polygon(surface, INK, points, max(1, round(2 * unit)))
                pygame.draw.circle(surface, (210, 185, 140), player.pos, player.radius * .45)
            else:
                pygame.draw.circle(surface, color, player.pos, player.radius)
                pygame.draw.circle(surface, (210, 185, 140), player.pos, player.radius * .6)
            if player.invulnerability > 0:
                pygame.draw.circle(surface, (250, 250, 190), player.pos, player.radius * 1.7, max(1, round(3 * unit)))
            if player.joust:
                pygame.draw.line(surface, (250, 230, 150), player.pos, player.pos + forward * 54 * unit, max(2, round(8 * unit)))
            self.text(surface, str(player.player_id), player.pos + pygame.Vector2(0, -player.radius - 15 * unit), 20, centered=True)
        for effect in self.effects:
            if effect.kind == "laser":
                pygame.draw.line(surface, (140, 225, 245), effect.start, effect.end, max(2, round(7 * unit)))
            else:
                radius = effect.end.x * (1 - effect.lifetime / (.35 if effect.kind == "splash" else .3))
                pygame.draw.circle(surface, (180, 210, 240) if effect.kind == "splash" else (235, 205, 140), effect.start,
                                   max(1, round(radius)), max(1, round(4 * unit)))
        pygame.draw.rect(surface, (242, 249, 230), (8 * unit, 8 * unit, self.width - 16 * unit, 48 * unit), border_radius=max(1, round(8 * unit)))
        for index, player in enumerate(self.players.values()):
            x = (22 + index * 420) * unit
            state = f"RELOAD {player.reload:.1f}s" if player.reload else f"ROCKS {player.ammo}"
            if player.state != "canoe":
                state = "THRUST: BUTTON 1" if player.state == "beaver" else "OUT"
            self.text(surface, f"P{player.player_id}  {self.scores[player.player_id]}/{self.setting('winning_score', 5)}   {state}", (x, 15 * unit), 21)
            self.text(surface, f"B2: {player.powerup or 'find a barrel'}", (x, 35 * unit), 16)
        if self.blocked or self.phase != "playing":
            box = pygame.FRect(self.width * .15, self.height * .38, self.width * .7, self.height * .22)
            pygame.draw.rect(surface, (250, 247, 220), box, border_radius=max(1, round(15 * unit)))
            message = self.error if self.blocked else (f"PLAYER {self.winner} WINS!" if self.winner is not None else "DRAW")
            self.text(surface, message, (self.width / 2, self.height * .45), 44, centered=True)
            subtitle = "Erase or move a physical obstacle" if self.blocked else (
                "First to five! Return to lobby to play again" if self.phase == "match_over" else f"Next round in {max(1, math.ceil(self.round_timer))}")
            self.text(surface, subtitle, (self.width / 2, self.height * .53), 23, centered=True)
