"""Computer opponents, so every game can be played by one person (or finished when a controller dies mid-demo).

A bot sees what a player sees, the game's state, and answers with what a player gives: a PlayerInput, an aim and two
buttons. Nothing in a game knows it is playing a bot, so a bot can stand in for either controller. Each is good
enough to be a game and clumsy enough to lose: it reacts late, aims a little off, and hesitates.

Treasure Dash needs nothing from here: its own bots draw and run a route (Treasure.new_match's `bots`).
"""

import math
import random
from collections import deque

import cv2
import numpy as np
import pygame

from beaver_battle.dam import CELL
from beaver_battle.model import PlayerInput


class BattleBot:
    """Beaver Battle: steer so the bow points where the other canoe is going, tap a rock off when it does, keep a
    little distance, swerve from rocks coming in, go for a lily pad when there is no shot, and swim away when sunk."""

    def __init__(self, player_id, seed=None):
        self.player_id, self.rng = player_id, random.Random(seed)
        self.clock, self.next_shot, self.pressed = 0.0, 1.5, False
        self.drift, self.drift_until, self.side = 0.0, 0.0, 1
        self.aim, self.anchor, self.anchored, self.escape, self.escape_until = None, None, 0.0, None, 0.0

    def clear_shot(self, game, start, end):
        """Nothing drawn, and no floating log, between the bow and the target."""
        gap = end - start
        steps = max(1, int(gap.length() // (16 * game.scale)))
        for step in range(2, steps - 1):
            point = start + gap * (step / steps)
            x, y = int(point.x), int(point.y)
            if game.walls is not None and 0 <= x < game.walls.shape[1] and 0 <= y < game.walls.shape[0] and game.walls[y, x]:
                return False
            if any(prop.hp > 0 and game.overlaps_prop(point, 5 * game.scale, prop) for prop in game.props):
                return False
        return True

    def step(self, game, dt):
        self.clock += dt
        me = game.players.get(self.player_id)
        idle = PlayerInput(self.player_id, self.aim, False, False)
        if me is None or me.state == "eliminated" or game.phase != "playing":
            return idle
        foes = [player for player in game.players.values() if player.player_id != self.player_id and player.state != "eliminated"]
        if not foes:
            return idle
        foe = min(foes, key=lambda player: player.pos.distance_squared_to(me.pos))
        unit = game.scale
        if self.clock >= self.drift_until:
            # A hand is never quite steady: the aim wanders a few degrees and settles somewhere new now and then.
            self.drift, self.drift_until = math.radians(self.rng.uniform(-7, 7)), self.clock + self.rng.uniform(.5, 1.3)
            self.side = self.rng.choice((-1, 1))
        gap = foe.pos - me.pos
        distance = max(1.0, gap.length())
        if me.state == "beaver":
            # In the water with no canoe: get away, and sprint if the other boat is close enough to ram.
            away = me.pos - gap.normalize() * 300 * unit
            away.x, away.y = min(max(away.x, 80 * unit), game.width - 80 * unit), min(max(away.y, 110 * unit), game.height - 80 * unit)
            self.aim = tuple(away)
            return PlayerInput(self.player_id, self.aim, distance < 320 * unit, False)
        # Lead the target by the time a rock takes to get there.
        speed = game.setting("rock_speed", 650) * unit
        ahead = pygame.Vector2(math.cos(foe.heading), math.sin(foe.heading)) * foe.speed * (distance / speed) if foe.state == "canoe" else pygame.Vector2()
        mark = foe.pos + ahead
        want = (mark - me.pos).rotate_rad(self.drift)
        loaded = me.cooldown == 0 and (me.ammo > 0 or me.powerup) and self.clock >= self.next_shot - .25
        threat = next((rock for rock in game.rocks if rock.owner != self.player_id and rock.velocity.length_squared() and
                       (me.pos - rock.pos).dot(rock.velocity) > 0 and (me.pos - rock.pos).length() < 330 * unit and
                       abs((me.pos - rock.pos).cross(rock.velocity.normalize())) < 46 * unit), None)
        if threat is not None:
            # A rock on its way: turn across its path.
            side = pygame.Vector2(-threat.velocity.y, threat.velocity.x).normalize()
            want = side * (1 if side.dot(me.pos - threat.pos) >= 0 else -1) * 260 * unit
        elif (me.ammo == 0 or not self.clear_shot(game, me.pos, mark)) and game.pickups and not me.powerup:
            pad = min(game.pickups, key=lambda pickup: pickup.pos.distance_squared_to(me.pos))
            want = pad.pos - me.pos
        elif not loaded and distance < 380 * unit and foe.state == "canoe":
            # Between shots a canoe never stops, so circle the other boat rather than run into it, and swing the bow
            # back onto it when the next rock is ready.
            want = want.rotate(self.side * (80 if distance > 200 * unit else 110))
        want = want.normalize() if want.length_squared() else pygame.Vector2(1, 0)
        if threat is None and not loaded:
            # Keep off the banks: the nearer an edge, the more the course bends back toward open water.
            margin = 150 * unit
            shove = pygame.Vector2(max(0.0, margin - me.pos.x) - max(0.0, me.pos.x - (game.width - margin)),
                                   max(0.0, margin + 40 * unit - me.pos.y) - max(0.0, me.pos.y - (game.height - margin))) / margin
            want = (want + shove * 1.6).normalize() if (want + shove * 1.6).length_squared() else want
        # Wedged against a rock or in a corner, a canoe sits still: notice, and paddle out toward open water.
        if self.anchor is None or me.pos.distance_to(self.anchor) > 12 * unit:
            self.anchor, self.anchored = me.pos.copy(), self.clock
        if self.clock - self.anchored > .6 and self.clock >= self.escape_until:
            # Straight back off whatever it is touching; with nothing to go by, toward the middle of the board.
            out = -me.wall if me.wall.length_squared() else pygame.Vector2(game.width / 2, game.height / 2) - me.pos
            self.escape = (out.normalize() if out.length_squared() else pygame.Vector2(1, 0)).rotate(self.rng.uniform(-40, 40))
            self.escape_until, self.anchored = self.clock + .9, self.clock
        escaping = self.clock < self.escape_until
        if escaping:
            want = self.escape
        target = me.pos + want * 260 * unit
        self.aim = (target.x, target.y)
        # One tap, one rock: only when the bow is nearly on the mark, the way is clear, and not in a flurry.
        bearing = math.atan2(mark.y - me.pos.y, mark.x - me.pos.x)
        off = abs((bearing - me.heading + math.pi) % math.tau - math.pi)
        ready = me.cooldown == 0 and (me.ammo > 0 or me.powerup) and self.clock >= self.next_shot
        shoot = ready and not escaping and threat is None and off < math.radians(9) and distance < 760 * unit and self.clear_shot(game, me.pos, mark)
        if shoot and not self.pressed:
            self.next_shot = self.clock + self.rng.uniform(.55, 1.1)
        tap = shoot and not self.pressed
        self.pressed = bool(shoot)
        return PlayerInput(self.player_id, self.aim, False, bool(shoot), special_pressed=bool(tap))


class DamBuilder:
    """Dam It!, building: one reasonable dam, drawn stroke by stroke at a marker's pace, then "done"."""

    def __init__(self, player_id, seed=None):
        self.player_id, self.rng = player_id, random.Random(seed)  # No seed: a different dam every round.
        self.strokes, self.along, self.wait, self.round = None, 0.0, 1.0, None

    def plan(self, game):
        unit, rng = game.scale, self.rng
        walls = [rng.choice((190, 300, 420, 560, 680))]
        if rng.random() < .3:
            walls.append(walls[0] + rng.choice((240, 320)))  # Sometimes a second wall: more wood, so a weaker one.
        lean = rng.uniform(-50, 50)
        return [(pygame.Vector2((x - lean) * unit, 60 * unit), pygame.Vector2((x + lean) * unit, game.height - 4)) for x in walls if x * unit < game.lodge.x - 200 * unit]

    def step(self, game, dt):
        if self.round != game.rounds:
            self.round, self.strokes, self.along, self.wait = game.rounds, self.plan(game), 0.0, 1.0
        self.wait -= dt
        if self.wait > 0:
            return PlayerInput(self.player_id, None, False, False)
        if not self.strokes:
            return PlayerInput(self.player_id, tuple(game.lodge), False, True)  # The second button: done.
        first, second = self.strokes[0]
        self.along += 520 * game.scale * dt / max(1.0, first.distance_to(second))
        if self.along >= 1:
            self.strokes.pop(0)
            self.along, self.wait = 0.0, .4
            return PlayerInput(self.player_id, tuple(second), False, False)  # Lift the pen between strokes.
        return PlayerInput(self.player_id, tuple(first.lerp(second, self.along)), True, False)


class DamAttacker:
    """Dam It!, attacking. Of the pieces it can actually get to, it breaches where the flood would come soonest, walks
    round wood and through its own gaps by the shortest way, and keeps biting. `delay` is the moment it takes to read
    the board and `fumble` loses one frame in so many to steering and lining up a bite, as a person does; with
    neither it is the perfect attacker that checks/check_dam.py measures dams against."""

    def __init__(self, player_id, delay=0.0, fumble=0, think=.5):
        self.player_id, self.delay, self.fumble, self.think = player_id, delay, fumble, think
        self.elapsed, self.frame, self.replan = 0.0, 0, 0.0
        self.target, self.goal, self.trail = None, None, []
        self.shunned, self.idle, self.last, self.biting = set(), 0.0, None, False

    def choose(self, game):
        rows, columns = game.wet.shape
        open_ground = ~game.blocked_grid()
        # Walk a cell clear of wood and rock: a route that brushes them snags on the corners.
        lanes = open_ground & ~cv2.dilate((~open_ground).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        grid = game.sections[:rows * CELL, :columns * CELL].reshape(rows, CELL, columns, CELL).max(axis=(1, 3))
        home = (min(rows - 1, int(game.beaver.y) // CELL), min(columns - 1, int(game.beaver.x) // CELL))
        best, self.target = math.inf, None
        count, regions = cv2.connectedComponents(open_ground.astype(np.uint8), connectivity=4)
        # Plan along the clear lanes first; if nothing worth chewing can be reached that way (the only way on is
        # through a narrow gap it made itself), plan over all open ground instead.
        for ground in (lanes, open_ground):
            steps = np.full(open_ground.shape, -1, np.int32)
            steps[home] = 0
            queue = deque([home])
            while queue:
                y, x = queue.popleft()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < rows and 0 <= nx < columns and steps[ny, nx] < 0 and ground[ny, nx]:
                        steps[ny, nx] = steps[y, x] + 1
                        queue.append((ny, nx))
            # A piece is worth chewing only while it still keeps two stretches of open ground apart: the river
            # from the dry side, or the beaver from the next wall. Once a wall is breached the rest is just wood.
            reachable, useful = set(), set()
            for section in set(np.unique(grid).tolist()) - {0}:
                around = cv2.dilate((grid == section).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & open_ground
                if (cv2.dilate((grid == section).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & (steps >= 0)).any():
                    reachable.add(section)
                    if len(set(np.unique(regions[around]).tolist()) - {0}) >= 2:
                        useful.add(section)
            if useful - self.shunned:
                break
        for section in ((reachable & useful) or reachable) - self.shunned or reachable:
            # Stand on the nearest open cell that touches this piece.
            beside = cv2.dilate((grid == section).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & (steps >= 0)
            where = np.argwhere(beside)
            stand = where[np.argmin(steps[beside])]
            spot = pygame.Vector2(float(stand[1] * CELL + CELL / 2), float(stand[0] * CELL + CELL / 2))
            cost = steps[tuple(stand)] * CELL / game.setting("beaver_speed", 200) + game.strength[section][0] * game.setting("bite_seconds", .3) \
                + spot.distance_to(game.lodge) / game.setting("flow_speed", 120)
            # Carry on with whatever is already being chewed: bites stick to a piece until it breaks.
            cost -= 6 if section == game.chewing and game.strength[section][0] < game.strength[section][1] else 0
            # Wood hard against a bank is awkward to stand at; a person goes for the open middle of a wall.
            cost += 3 if game.solid[max(0, int(spot.y) - 45):int(spot.y) + 45, max(0, int(spot.x) - 45):int(spot.x) + 45].any() else 0
            if cost < best:
                ys, xs = np.nonzero(game.sections == section)
                best, self.target, self.goal = cost, section, (stand, pygame.Vector2(float(xs.mean()), float(ys.mean())), np.column_stack([xs, ys]))
        self.trail = []
        if self.target is not None:
            cell = tuple(self.goal[0])
            while steps[cell] > 0:
                self.trail.append(cell)
                y, x = cell
                cell = min(((ny, nx) for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)) if 0 <= ny < rows and 0 <= nx < columns and steps[ny, nx] >= 0),
                           key=lambda spot: steps[spot])
            self.trail.reverse()

    def step(self, game, dt):
        """The input for this frame, or None for a frame lost to fumbling."""
        self.replan -= dt
        if self.target is None or self.target not in game.strength or game.strength[self.target][0] <= 0 or self.replan <= 0:
            self.replan = self.think
            self.choose(game)
        aim = tuple(game.beaver)
        if self.target is not None:
            while len(self.trail) > 1 and pygame.Vector2(self.trail[0][1] * CELL + CELL / 2, self.trail[0][0] * CELL + CELL / 2).distance_to(game.beaver) < 14:
                self.trail.pop(0)
            waypoint = pygame.Vector2(self.trail[0][1] * CELL + CELL / 2, self.trail[0][0] * CELL + CELL / 2) if len(self.trail) > 1 else self.goal[1]
            aim = tuple(waypoint)
        # A piece tucked against the bank can be in reach on the map and not on the ground. Standing still without
        # biting for a second means this one cannot be got at: try another.
        self.idle = self.idle + dt if self.last is not None and self.last.distance_to(game.beaver) < .5 and not self.biting else 0.0
        self.last = game.beaver.copy()
        if self.idle > 1 and self.target is not None:
            self.shunned.add(self.target)
            self.target, self.idle = None, 0.0
        self.frame += 1
        fumbled = self.elapsed < self.delay or (self.fumble and self.frame % self.fumble == 0)
        self.elapsed += dt
        # Bite only at the chosen piece: holding the button down on the way there gnaws at whatever is alongside.
        self.biting = self.target is not None and float(np.hypot(self.goal[2][:, 0] - game.beaver.x, self.goal[2][:, 1] - game.beaver.y).min()) <= game.setting("bite_reach", 34)
        return None if fumbled else PlayerInput(self.player_id, aim, self.biting, False)


class DamBot:
    """Whichever job falls to the bot this round."""

    def __init__(self, player_id):
        self.player_id, self.builder, self.attacker, self.round = player_id, DamBuilder(player_id), None, None

    def step(self, game, dt):
        if game.phase == "building" and game.builder == self.player_id:
            return self.builder.step(game, dt)
        if game.phase == "attack" and game.attacker == self.player_id:
            if self.round != game.rounds:
                # Slow off the mark and working at half speed: the best dams beat it, a fair one is a close thing,
                # and a careless one floods. (The clock itself is tuned against a far more careful attacker.)
                self.round, self.attacker = game.rounds, DamAttacker(self.player_id, delay=2.0, fumble=2, think=1.2)
            return self.attacker.step(game, dt)
        return None
