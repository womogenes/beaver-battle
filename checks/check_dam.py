"""Dam It!: the rules hold, and neither role is favoured.

Balance is tested by playing it. A bot attacker that always goes for the quickest flood is set against a range of
bot dams; the dam that holds longest against it is the builder's best play, and attack_seconds has to sit where that
contest is a coin flip decided by execution, not by which role you were given.
"""

import copy
import math
import tomllib

import cv2
import numpy as np
import pygame

from beaver_battle import sound
from beaver_battle.dam import CELL, DamIt
from beaver_battle.model import PlayerInput

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)
rules = config["dam"]


def fresh(limit=None):
    settings = copy.deepcopy(config)
    if limit is not None:
        settings["dam"]["attack_seconds"] = limit
    game = DamIt(settings)
    game.new_match((1, 2), swap=False)
    return game


def wall(game, x, lean=0):
    game.scribble(pygame.Vector2(x - lean, 0), pygame.Vector2(x + lean, game.height))


def arc(game, radius):
    """A curve from bank to bank round the river side of the lodge."""
    points = [game.lodge + pygame.Vector2(-math.cos(a), math.sin(a)) * radius for a in np.linspace(-1.35, 1.35, 40)]
    for first, second in zip(points, points[1:]):
        game.scribble(first, second)


def sealed(game):
    game.timer = 0
    game.update(1 / 60, {})
    while game.phase == "rising":
        game.update(1 / 60, {})
    return game


def attack(game, limit=90.0):
    """Play the attacker well. Of the pieces it can actually get to, it breaches where the flood would come soonest,
    walks round wood and through its own gaps by the shortest way, and never stops biting. Returns the seconds the
    flood took, or None if the lodge stayed dry."""
    from collections import deque
    elapsed, target, goal, trail, replan = 0.0, None, None, [], 0.0
    rows, columns = game.wet.shape
    while game.phase == "attack" and elapsed < limit:
        replan -= 1 / 60
        if target is None or target not in game.strength or game.strength[target][0] <= 0 or replan <= 0:
            replan = .5
            open_ground = ~game.blocked_grid()
            grid = game.sections[:rows * CELL, :columns * CELL].reshape(rows, CELL, columns, CELL).max(axis=(1, 3))
            home = (min(rows - 1, int(game.beaver.y) // CELL), min(columns - 1, int(game.beaver.x) // CELL))
            steps = np.full(open_ground.shape, -1, np.int32)
            steps[home] = 0
            queue = deque([home])
            while queue:
                y, x = queue.popleft()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < rows and 0 <= nx < columns and steps[ny, nx] < 0 and open_ground[ny, nx]:
                        steps[ny, nx] = steps[y, x] + 1
                        queue.append((ny, nx))
            best, target = math.inf, None
            # Only wood with water against it is worth chewing; failing that (an inner wall the river has not reached
            # yet), whatever stands between the beaver and the rest.
            wash = cv2.dilate(game.wet.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            pressed = set(np.unique(grid[wash]).tolist()) - {0}
            reachable = {section for section in set(np.unique(grid).tolist()) - {0}
                         if (cv2.dilate((grid == section).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & (steps >= 0)).any()}
            for section in (reachable & pressed) or reachable:
                # Stand on the nearest open cell that touches this piece.
                beside = cv2.dilate((grid == section).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & (steps >= 0)
                where = np.argwhere(beside)
                stand = where[np.argmin(steps[beside])]
                spot = pygame.Vector2(float(stand[1] * CELL + CELL / 2), float(stand[0] * CELL + CELL / 2))
                cost = steps[tuple(stand)] * CELL / rules["beaver_speed"] + game.strength[section][0] * rules["bite_seconds"] + spot.distance_to(game.lodge) / rules["flow_speed"]
                if cost < best:
                    ys, xs = np.nonzero(game.sections == section)
                    best, target, goal = cost, section, (stand, pygame.Vector2(float(xs.mean()), float(ys.mean())))
            trail = []
            if target is not None:
                cell = tuple(goal[0])
                while steps[cell] > 0:
                    trail.append(cell)
                    y, x = cell
                    cell = min(((ny, nx) for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)) if 0 <= ny < rows and 0 <= nx < columns and steps[ny, nx] >= 0),
                               key=lambda spot: steps[spot])
                trail.reverse()
        aim = tuple(game.beaver)
        if target is not None:
            while len(trail) > 1 and pygame.Vector2(trail[0][1] * CELL + CELL / 2, trail[0][0] * CELL + CELL / 2).distance_to(game.beaver) < 14:
                trail.pop(0)
            waypoint = pygame.Vector2(trail[0][1] * CELL + CELL / 2, trail[0][0] * CELL + CELL / 2) if len(trail) > 1 else goal[1]
            aim = tuple(waypoint)
        game.update(1 / 60, {2: PlayerInput(2, aim, True, False)})
        elapsed += 1 / 60
    return elapsed if game.winner == 2 else None


# Wood is shared strength: the whole dam is worth the same number of bites however much is drawn, a piece's share is
# its area, and no piece is a sliver.
for build in (lambda game: wall(game, 500), lambda game: (wall(game, 300), wall(game, 700)), lambda game: [wall(game, 500 + offset) for offset in range(0, 60, 9)]):
    game = fresh()
    build(game)
    sealed(game)
    total = sum(left for left, whole in game.strength.values())
    assert abs(total - rules["strength"]) < rules["strength"] * .06, total
    assert min(whole for left, whole in game.strength.values()) > .3 * total / len(game.strength), "a sliver is a free breach"
thin, thick = fresh(), fresh()
wall(thin, 500)
[wall(thick, 500 + offset) for offset in range(0, 40, 9)]
assert thick.bites_per_piece() < thin.bites_per_piece() / 3, "more wood, weaker pieces"

# A dam from bank to bank holds the river; one with a real gap leaks and loses before the attack begins; a hairline
# gap, or a stroke that stops just short of the bank, is sealed for the builder.
game = sealed((lambda g: (wall(g, 500), g)[1])(fresh()))
assert game.phase == "attack" and game.flooded == 0
leaky = fresh()
leaky.scribble(pygame.Vector2(500, 0), pygame.Vector2(500, 330))
leaky.scribble(pygame.Vector2(500, 400), pygame.Vector2(500, 720))
assert sealed(leaky).winner == 2 and leaky.verdict == "THE DAM LEAKED!"
mended = fresh()
mended.scribble(pygame.Vector2(500, 150), pygame.Vector2(500, 350))
mended.scribble(pygame.Vector2(500, 350 + 2 * rules["merge_gap"] - 4), pygame.Vector2(500, 610))
assert sealed(mended).phase == "attack"
empty = fresh()
assert sealed(empty).winner == 2, "no dam at all is a flood"

# No wood by the lodge or in the river mouth, and never more than the allowance.
game = fresh()
game.scribble(game.lodge + (-40, -200), game.lodge + (-40, 200))
assert not game.ink[int(game.lodge.y), int(game.lodge.x) - 40]
for offset in range(0, 900, 9):
    wall(game, 200 + offset)
assert game.wood_length() <= rules["max_wood"] + game.height

# A closed shape is a log and an open stroke a stick.
game = fresh()
wall(game, 400)
for first, second in zip([(600, 250), (760, 250), (760, 470), (600, 470), (600, 250)], [(760, 250), (760, 470), (600, 470), (600, 250), (760, 250)]):
    game.scribble(pygame.Vector2(first), pygame.Vector2(second))
sealed(game)
assert game.logs[360, 680] and game.wood[360, 680] and not game.logs[360, 400] and game.wood[360, 400]

# The beaver follows the laser, cannot walk through wood, chews only within reach, and a broken piece lets water by.
game = sealed((lambda g: (wall(g, 900), g)[1])(fresh(60)))
start = game.beaver.copy()
for step in range(120):
    game.update(1 / 60, {2: PlayerInput(2, (300, 380), False, False)})
assert 905 < game.beaver.x < start.x and game.flooded == 0, "stopped by the dam"
before = sum(left for left, whole in game.strength.values())
for step in range(60):
    game.update(1 / 60, {2: PlayerInput(2, (300, 380), True, False)})
spent = before - sum(left for left, whole in game.strength.values() if left > 0) - 0
assert "chomp" in game.sounds and 2 <= round(1 / rules["bite_seconds"]) - 1 <= spent + 1
far = sealed((lambda g: (wall(g, 300), g)[1])(fresh(60)))
for step in range(30):
    far.update(1 / 60, {2: PlayerInput(2, tuple(far.beaver), True, False)})
assert sum(left for left, whole in far.strength.values()) == sum(whole for left, whole in far.strength.values()), "out of reach"
assert attack(game) is not None and "crack" in game.sounds and game.verdict == "THE LODGE IS FLOODED!"
idle = sealed((lambda g: (wall(g, 500), g)[1])(fresh(3)))
for step in range(60 * 4):
    idle.update(1 / 60, {})
assert idle.winner == 1 and idle.verdict == "THE LODGE STAYED DRY!"

# Roles swap from round to round.
game = DamIt(config)
game.new_match((1, 2))
first = (game.builder, game.attacker)
game.new_match((1, 2))
assert (game.builder, game.attacker) == first[::-1]

# Balance. Every sensible dam, against the same good attacker, with all the time in the world:
plans = {"far wall": lambda g: wall(g, 170), "upstream wall": lambda g: wall(g, 380), "middle wall": lambda g: wall(g, 600),
         "downstream wall": lambda g: wall(g, 820), "arc by the lodge": lambda g: arc(g, 165), "slanted wall": lambda g: wall(g, 500, 140),
         "two walls": lambda g: (wall(g, 300), wall(g, 760)), "far and near": lambda g: (wall(g, 170), arc(g, 165)),
         "thick wall": lambda g: [wall(g, 600 + offset) for offset in range(0, 30, 9)]}
times = {}
for name, plan in plans.items():
    game = fresh(999)
    plan(game)
    sealed(game)
    assert game.phase == "attack", f"{name} should hold water"
    times[name] = attack(game, 120)
    assert times[name] is not None, name
best = max(times.values())
spread = sorted(times.values())
limit = rules["attack_seconds"]
# The builder's best dam must make it a close thing either way: a perfect attacker just gets there, so a human one,
# who loses a little time everywhere, is up against it, and a builder who draws carelessly loses.
assert .9 * best <= limit <= 1.15 * best, f"attack_seconds {limit} against a best dam of {best:.1f} s: {times}"
assert sum(seconds > limit * .85 for seconds in times.values()) >= 2, "more than one dam should be worth building"
assert sum(seconds < limit * .8 for seconds in times.values()) >= 2, "and a poor choice of dam should lose"

# Rendering each phase works, and every sound named exists.
surface = pygame.Surface((1280, 720))
game = fresh()
game.draw(surface)
wall(game, 500)
game.draw(surface)
sealed(game).draw(surface)
attack(game)
game.draw(surface)
names = {word for line in open("beaver_battle/dam.py") if "sounds" in line and ("append" in line or "+=" in line or "= [" in line)
         for word in __import__("re").findall(r'"(\\w+)"', line)} - {"go"} | {"go"}
assert names <= set(sound.build()), names - set(sound.build())

print("Dam checks passed: shared strength, no slivers, sealing and leaks, keep-out and allowance, logs and sticks, the beaver, "
      f"role swap, rendering, sounds; balance: floods take {', '.join(f'{name} {seconds:.0f} s' for name, seconds in sorted(times.items(), key=lambda item: item[1]))}; "
      f"attack time {limit:g} s")
