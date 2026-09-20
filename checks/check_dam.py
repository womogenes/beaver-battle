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
from beaver_battle.dam import CELL, MAPS, DamIt
from beaver_battle.model import PlayerInput

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)
rules = config["dam"]


def fresh(limit=None, board=0):
    settings = copy.deepcopy(config)
    if limit is not None:
        settings["dam"]["attack_seconds"] = limit
    game = DamIt(settings)
    game.new_match((1, 2), swap=False, board=board)
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


def attack(game, limit=90.0, human=False):
    """Play the attacker well. Of the pieces it can actually get to, it breaches where the flood would come soonest,
    walks round wood and through its own gaps by the shortest way, and never stops biting. Returns the seconds the
    flood took, or None if the lodge stayed dry."""
    from collections import deque
    # human=True plays it as a person does: a moment to take the board in, and one frame in five lost to steering
    # and lining up the bite.
    elapsed, target, goal, trail, replan = 0.0, None, None, [], 0.0
    frame, shunned, idle, last = 0, set(), 0.0, None
    rows, columns = game.wet.shape
    while game.phase == "attack" and elapsed < limit:
        replan -= 1 / 60
        if target is None or target not in game.strength or game.strength[target][0] <= 0 or replan <= 0:
            replan = .5
            open_ground = ~game.blocked_grid()
            # Walk a cell clear of wood and rock: a route that brushes them snags on the corners.
            lanes = open_ground & ~cv2.dilate((~open_ground).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            grid = game.sections[:rows * CELL, :columns * CELL].reshape(rows, CELL, columns, CELL).max(axis=(1, 3))
            home = (min(rows - 1, int(game.beaver.y) // CELL), min(columns - 1, int(game.beaver.x) // CELL))
            best, target = math.inf, None
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
                if useful - shunned:
                    break
            pressed = useful
            for section in ((reachable & pressed) or reachable) - shunned or reachable:
                # Stand on the nearest open cell that touches this piece.
                beside = cv2.dilate((grid == section).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & (steps >= 0)
                where = np.argwhere(beside)
                stand = where[np.argmin(steps[beside])]
                spot = pygame.Vector2(float(stand[1] * CELL + CELL / 2), float(stand[0] * CELL + CELL / 2))
                cost = steps[tuple(stand)] * CELL / rules["beaver_speed"] + game.strength[section][0] * rules["bite_seconds"] + spot.distance_to(game.lodge) / rules["flow_speed"]
                # Carry on with whatever is already being chewed: bites stick to a piece until it breaks.
                cost -= 6 if section == game.chewing and game.strength[section][0] < game.strength[section][1] else 0
                # Wood hard against a bank is awkward to stand at; a person goes for the open middle of a wall.
                cost += 3 if game.solid[max(0, int(spot.y) - 45):int(spot.y) + 45, max(0, int(spot.x) - 45):int(spot.x) + 45].any() else 0
                if cost < best:
                    ys, xs = np.nonzero(game.sections == section)
                    best, target, goal = cost, section, (stand, pygame.Vector2(float(xs.mean()), float(ys.mean())), np.column_stack([xs, ys]))
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
        # A piece tucked against the bank can be in reach on the map and not on the ground. Standing still without
        # biting for a second means this one cannot be got at: try another.
        idle = idle + 1 / 60 if last is not None and last.distance_to(game.beaver) < .5 and not biting else 0.0
        last = game.beaver.copy()
        if idle > 1 and target is not None:
            shunned.add(target)
            target, idle = None, 0.0
        frame += 1
        fumbled = human and (elapsed < 1.2 or frame % 5 == 0)
        # Bite only at the chosen piece: holding the button down on the way there gnaws at whatever is alongside.
        biting = target is not None and float(np.hypot(goal[2][:, 0] - game.beaver.x, goal[2][:, 1] - game.beaver.y).min()) <= rules["bite_reach"]
        game.update(1 / 60, {} if fumbled else {2: PlayerInput(2, aim, biting, False)})
        elapsed += 1 / 60
    return elapsed if game.winner == 2 else None


# Wood is shared strength: a piece's share is its area, no piece is a sliver, one clean wall gets the full strength and
# more wood than that gets less in total, so piling it on is worse than useless.
totals = []
for build in (lambda game: wall(game, 500), lambda game: (wall(game, 300), wall(game, 700)), lambda game: [wall(game, 500 + offset) for offset in range(0, 60, 9)]):
    game = fresh()
    build(game)
    sealed(game)
    total = sum(left for left, whole in game.strength.values())
    due = game.total_strength(game.wood_length(game.wood))
    assert due * .97 <= total <= due * 1.15, (total, due)
    assert min(whole for left, whole in game.strength.values()) > .3 * total / len(game.strength), "a sliver is a free breach"
    totals.append(total)
assert totals[0] > .9 * rules["strength"] and totals[1] < .65 * totals[0] and totals[2] < .3 * totals[0], totals
thin, thick = fresh(), fresh()
wall(thin, 500)
[wall(thick, 500 + offset) for offset in range(0, 40, 9)]
assert thick.bites_per_piece() < thin.bites_per_piece() / 3, "more wood, weaker pieces"
# A slab is cut straight across, never into layers: any one piece gone and the river is through it.
slab = fresh()
[wall(slab, 500 + offset) for offset in range(0, 120, 9)]
sealed(slab)
assert len(slab.strength) <= 8, len(slab.strength)
for section in slab.strength:
    ground = ~(slab.solid | (slab.wood & (slab.sections != section)))
    count, regions = cv2.connectedComponents(ground.astype(np.uint8), connectivity=4)
    assert regions[int(slab.lodge.y), int(slab.lodge.x) - 90] == regions[360, 300], f"piece {section} does not span the slab"
# ...and each of those wide pieces is still weaker than one short length of a plain wall.
assert max(whole for left, whole in slab.strength.values()) < .75 * max(whole for left, whole in sealed((lambda g: (wall(g, 500), g)[1])(fresh())).strength.values())

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

# Roles swap from round to round, and the map moves on once both players have built on it (or when asked).
game = DamIt(config)
game.new_match((1, 2))
first = (game.builder, game.attacker)
game.new_match((1, 2))
assert (game.builder, game.attacker) == first[::-1]
boards = [game.board_index]
for round_number in range(4):
    game.new_match((1, 2))
    boards.append(game.board_index)
assert boards == [0, 1, 1, 2, 2], boards
game.new_match((1, 2), board=game.board_index + 1)
assert game.board_index == 3 and game.board_rounds == 1
game.new_match((1, 2), board=game.board_index + 1)
assert game.board_index == 0, "the maps go round"

# Every bite takes a visible block out of the piece, but the piece holds water until its last one.
game = sealed((lambda g: (wall(g, 900), g)[1])(fresh(60)))
for step in range(60 * 3):
    game.update(1 / 60, {2: PlayerInput(2, (300, game.lodge.y), True, False)})
bitten = [section for section, (left, whole) in game.strength.items() if 0 < left < whole]
assert bitten and game.eaten is not None and game.eaten.any() and not (game.eaten & ~game.wood).any()
assert (game.eaten & (game.sections == bitten[0])).sum() < .9 * (game.sections == bitten[0]).sum(), "something is always left standing"
assert game.flooded == 0 and not game.passable(pygame.Vector2(900, game.lodge.y)), "bitten wood still holds"

# Every map holds water behind a plain wall, keeps its lodge and beaver on open ground, and lets the river reach the
# lodge when there is no dam.
for index, layout in enumerate(MAPS):
    game = fresh(60, index)
    assert not game.solid[int(game.lodge.y), int(game.lodge.x)] and not game.solid[int(game.beaver.y), int(game.beaver.x)], layout["name"]
    wall(game, 700)
    assert sealed(game).phase == "attack" and game.flooded == 0, f"{layout['name']}: a wall should hold"
    assert abs(game.timer - 60 * layout["clock"]) < 1, "each map has its own clock"
    assert sealed(fresh(60, index)).winner == 2, f"{layout['name']}: with no dam the lodge floods"

# Balance. Every kind of dam against the same attacker, one that reacts and aims like a person, with all the time in
# the world:
plans = {"far wall": lambda g: wall(g, 170), "upstream wall": lambda g: wall(g, 380), "middle wall": lambda g: wall(g, 600),
         "downstream wall": lambda g: wall(g, 820), "arc by the lodge": lambda g: arc(g, 165), "slanted wall": lambda g: wall(g, 500, 140),
         "two walls": lambda g: (wall(g, 300), wall(g, 760)), "far and near": lambda g: (wall(g, 170), arc(g, 165)),
         "three walls": lambda g: (wall(g, 250), wall(g, 520), wall(g, 790)),
         "thick wall": lambda g: [wall(g, 600 + offset) for offset in range(0, 30, 9)],
         "thick far wall": lambda g: [wall(g, 170 + offset) for offset in range(0, 30, 9)],
         "fat slab": lambda g: [wall(g, 560 + offset) for offset in range(0, 120, 9)]}
times = {}
for name, plan in plans.items():
    game = fresh(999)
    plan(game)
    sealed(game)
    assert game.phase == "attack", f"{name} should hold water"
    times[name] = attack(game, 120, True)
    assert times[name] is not None, name
best = max(times.values())
limit = rules["attack_seconds"]
# Piling wood on is the easy dam to beat, never the safe one.
assert times["thick wall"] < times["middle wall"] - 2 and times["fat slab"] < times["middle wall"] - 2, times
assert times["thick far wall"] < times["far wall"] - 1, times
assert max(times, key=times.get) not in ("thick wall", "thick far wall", "fat slab", "three walls"), times
# The attacker goes second and has the harder job, so the clock leans their way: the bot, who never hesitates over
# where to bite, beats the best dam with about a quarter of the time to spare, and a person is up against it.
assert 1.15 * best <= limit <= 1.4 * best, f"attack_seconds {limit} against a best dam of {best:.1f} s: {times}"
assert sum(seconds > limit * .75 for seconds in times.values()) >= 2, "more than one dam should be worth building"
assert sum(seconds < limit * .6 for seconds in times.values()) >= 2, "and a poor choice of dam should lose clearly"

# The other maps are kept as fair as the first: a few plain dams each, and the clock a quarter or so beyond the best.
map_times = {}
for index, layout in enumerate(MAPS[1:], 1):
    found = {}
    for name, plan in (("far wall", lambda g: wall(g, 170)), ("upstream wall", lambda g: wall(g, 430)), ("downstream wall", lambda g: wall(g, 830)), ("arc by the lodge", lambda g: arc(g, 165))):
        game = fresh(999, index)
        plan(game)
        sealed(game)
        assert game.phase == "attack", f"{layout['name']}: {name} should hold water"
        found[name] = attack(game, 120, True)
        assert found[name] is not None, (layout["name"], name)
    map_times[layout["name"]] = max(found.values())
    clock = rules["attack_seconds"] * layout["clock"]
    assert 1.1 * max(found.values()) <= clock <= 1.5 * max(found.values()), f"{layout['name']}: {clock:.0f} s against {found}"

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

print("Dam checks passed: four maps, block-by-block chewing, shared strength, excess wood weakens, slabs cut across, no slivers, sealing and leaks, keep-out and allowance, logs and sticks, the beaver, "
      f"role swap, rendering, sounds; balance: floods take {', '.join(f'{name} {seconds:.0f} s' for name, seconds in sorted(times.items(), key=lambda item: item[1]))}; "
      f"attack time {limit:g} s; other maps' best dams " + ", ".join(f"{name} {seconds:.0f} s" for name, seconds in map_times.items()))
