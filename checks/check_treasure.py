"""Treasure Dash: every board is a fair puzzle, ink becomes a route, and the race plays out by its rules."""

import copy
import math
import tempfile
import tomllib
from pathlib import Path

import numpy as np
import pygame

from beaver_battle import sound
from beaver_battle.leaderboard import Leaderboard
from beaver_battle.model import PlayerInput
from beaver_battle.treasure import BOARDS, SOLO_BOARDS, Cow, Treasure, cheapest_route

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)


bare = copy.deepcopy(config)
bare["treasure"].update(trees=0, logs=0, boost_clusters=0, cows=0)


def fresh(board, bots=(), dressed=False):
    """Rule tests run on a bare board, so a stray cow or log cannot change what they measure."""
    game = Treasure(config if dressed else bare)
    game.new_match((1, 2), bots, board)
    return game


def draw(game, runner, points):
    for first, second in zip(points, points[1:]):
        game.scribble(runner, pygame.Vector2(first), pygame.Vector2(second))


def run(game, seconds, inputs=None):
    for step in range(round(seconds * 60)):
        game.update(1 / 60, inputs() if callable(inputs) else (inputs or {}))


def tracing(game):
    return {player_id: PlayerInput(player_id, tuple(game.point_at(runner, runner.travelled + 50)), False, False)
            for player_id, runner in game.runners.items() if runner.route}


# Every board: the chest can be reached on foot round the rocks from both sides, the straight line cannot be
# walked (so a route has to be thought about), both sides are mirror images, and nothing covers a start or the chest.
for index, board in enumerate(BOARDS):
    game = fresh(index, dressed=True)
    left, right = game.runners[1], game.runners[2]
    assert left.start.x + right.start.x == game.width and left.start.y == right.start.y
    for runner in (left, right):
        assert game.rock_at(runner.start, 40) is None and not game.in_water(runner.start), board["name"]
        assert len(game.bot_plan(runner)) > 2, f'{board["name"]}: no way round the rocks'
        straight = [runner.start.lerp(game.chest, step / 200) for step in range(201)]
        blocked = any(game.rock_at(point, 8) is not None for point in straight)
        wet = sum(game.in_water(point) for point in straight) / len(straight)
        assert blocked or wet > .08, f'{board["name"]}: the straight line is free, so there is nothing to solve'
    assert game.rock_at(game.chest, 45) is None and not game.in_water(game.chest), board["name"]
    assert np.array_equal(game.water, game.water[:, ::-1]), board["name"]

# A small break in the ink is joined up; a big one leaves the chest out of reach and marks where the ink ran out.
game = fresh(0)
runner = game.runners[1]
y = runner.start.y - 250
whole = [runner.start, (runner.start.x, y), (game.chest.x, y), game.chest]
draw(game, runner, [whole[0], whole[1], (300, y)])
draw(game, runner, [(300 + 24, y), whole[2], whole[3]])
route, missed = game.find_route(runner, runner.ink)
assert route is not None and missed is None
game = fresh(0)
runner = game.runners[1]
draw(game, runner, [whole[0], whole[1], (300, y)])
draw(game, runner, [(300 + 90, y), whole[2], whole[3]])
route, missed = game.find_route(runner, runner.ink)
assert route is None and missed is not None and missed.x < 330

# No ink at all, or ink that never touches the start, is a broken path; the other player then wins outright.
game = fresh(0, bots=(2,))
run(game, config["treasure"]["draw_seconds"] + config["treasure"]["check_seconds"] + .3)
assert game.phase == "match_over" and game.winner == 2 and game.verdict == "BROKEN PATH"
assert game.runners[1].state == "broken" and "broken" in game.sounds

# A line through a rock stops the beaver at the rock for good; the other beaver carries on and wins.
game = fresh(0)
left, right = game.runners[1], game.runners[2]
draw(game, left, [left.start, game.chest])
draw(game, right, game.bot_plan(right))
game.judge()
run(game, config["treasure"]["check_seconds"] + .1)
assert game.phase == "running"
run(game, 12, lambda: tracing(game))
assert left.state == "stuck" and "bonk" in game.sounds and left.pos.x < game.rocks[0][0].x
run(game, 60, lambda: tracing(game))
assert game.phase == "match_over" and game.winner == 2 and game.verdict == "TREASURE!"

# The beaver only walks while its line is being traced, swims slower than it walks, and boosts on button 2.
game = fresh(1)
left = game.runners[1]
draw(game, left, game.bot_plan(left))
draw(game, game.runners[2], game.bot_plan(game.runners[2]))
game.judge()
run(game, config["treasure"]["check_seconds"] + .1)
run(game, 1, {1: PlayerInput(1, (10, 700), False, False)})
assert left.travelled == 0, "pointing away from the line must not move the beaver"
run(game, 2, lambda: tracing(game))
walked = left.travelled / 2
assert abs(walked - config["treasure"]["walk_speed"]) < 3
before = left.travelled
run(game, 1, lambda: {1: PlayerInput(1, tuple(game.point_at(left, left.travelled + 50)), True, False)})  # A click boosts.
assert left.travelled - before > walked * 1.3 and "boost" in game.sounds and left.cooldown > 0
while not game.in_water(left.pos) and game.phase == "running":
    run(game, .1, lambda: tracing(game))
assert left.state == "swimming" and "plunge" in game.sounds and game.particles
while left.boost > 0:
    run(game, .1, lambda: tracing(game))
before = left.travelled
run(game, 1, lambda: tracing(game))
assert abs((left.travelled - before) - config["treasure"]["swim_speed"]) < 3

# Two bots finish a race on every board, and a race takes about half a minute.
times = []
for index in range(len(BOARDS)):
    game = fresh(index, bots=(1, 2), dressed=True)
    elapsed = 0.0
    while game.phase != "match_over" and elapsed < 200:
        game.update(1 / 60, {})
        elapsed += 1 / 60
        if game.phase == "running" and not times[index:]:
            times.append(-elapsed)
    assert game.phase == "match_over" and game.verdict == "TREASURE!", (BOARDS[index]["name"], game.verdict)
    times[index] += elapsed
assert 5 < sum(times) / len(times) < 16, times

# A duel's scatter of trees, logs and boost tokens is mirrored, never seals anyone in, and changes from match to match;
# a one-player board is dressed the same way every time, so today's times are comparable.
game = fresh(6, dressed=True)
assert game.trees and game.logs and game.boosts and game.cows and game.portals
for things in (game.trees, [(first, half) for first, second, half in game.logs], [(place, alive) for place, alive in game.boosts]):
    spots = sorted((round(place.x), round(place.y)) for place, extra in things)
    assert spots == sorted((game.width - x, y) for x, y in spots), "a duel's fixed scatter must be mirrored"
assert game.passable()
again = fresh(6, dressed=True)
again.new_match((1, 2), (), 6)
assert [tuple(place) for place, size in again.trees] != [tuple(place) for place, size in game.trees]
first, second = Treasure(config), Treasure(config)
for solo_game in (first, second, second):
    solo_game.new_match((1,), (), 9, solo=True)
assert [tuple(place) for place, size in first.trees] == [tuple(place) for place, size in second.trees]
assert not np.array_equal(first.water, first.water[:, ::-1]), "one-player boards need not be symmetric"

# A tree or a log on the line stops a beaver for good, like a rock.
for kind in ("tree", "log"):
    game = fresh(0)
    left = game.runners[1]
    middle = left.start + (120, -150)
    if kind == "tree":
        game.trees = [(middle, 28)]
    else:
        game.logs = [(middle - (0, 40), middle + (0, 40), 10)]
    draw(game, left, [left.start, middle + (-200, 0) if False else middle, middle + (150, 0)])
    draw(game, left, [middle + (150, 0), (game.chest.x, middle.y), game.chest])
    draw(game, game.runners[2], game.bot_plan(game.runners[2]))
    game.judge()
    run(game, config["treasure"]["check_seconds"] + .1)
    run(game, 8, lambda: tracing(game))
    assert left.state == "stuck" and left.pos.distance_to(middle) < 60, kind

# A portal carries a path from one end to the other: ink to the first pad and ink from the second is a whole route,
# the hop costs no distance, and the beaver arrives on the far side with a sound.
game = fresh(6)
left = game.runners[1]
entry, exit_pad = next((first, second) for first, second, index in game.portals if first.x < game.width / 2)
draw(game, left, [left.start, (left.start.x, entry.y), entry])
draw(game, left, [exit_pad, (exit_pad.x + 40, game.chest.y - 60), game.chest])
route, missed = game.find_route(left, left.ink)
assert route is not None and len(left.jumps) == 1
draw(game, game.runners[2], game.bot_plan(game.runners[2]))
game.judge()
hop = left.jumps[0]
assert game.point_at(left, hop - 1).distance_to(entry) < 30 and game.point_at(left, hop + 1).distance_to(exit_pad) < 30
run(game, config["treasure"]["check_seconds"] + .1)
while left.travelled <= hop + 2 and game.phase == "running":
    run(game, .1, lambda: tracing(game))
assert "warp" in game.sounds and left.pos.distance_to(exit_pad) < 40
without = fresh(6)
without.portals = []
draw(without, without.runners[1], [left.start, (left.start.x, entry.y), entry])
draw(without, without.runners[1], [exit_pad, (exit_pad.x + 40, game.chest.y - 60), game.chest])
assert without.find_route(without.runners[1], without.runners[1].ink)[0] is None, "without the portal those two strokes do not connect"

# A cow in the way holds a beaver up only while it stands there; a boost token gives a burst of speed and is used up.
game = fresh(1)
left = game.runners[1]
draw(game, left, game.bot_plan(left))
draw(game, game.runners[2], game.bot_plan(game.runners[2]))
game.judge()
run(game, config["treasure"]["check_seconds"] + .1)
blocker = Cow(game.point_at(left, 45), game.point_at(left, 45), wait=99)
game.cows = [blocker]
run(game, 2, lambda: tracing(game))
assert left.travelled < 30 and left.held and left.state != "stuck" and "moo" in game.sounds
blocker.pos.update(40, 700)
blocker.target.update(40, 700)
run(game, 1, lambda: tracing(game))
assert left.travelled > 60 and not left.held
game.boosts = [[game.point_at(left, left.travelled + 30), True]]
run(game, 1, lambda: tracing(game))
assert not game.boosts[0][1] and "zip" in game.sounds and left.boost > 0

# Cows keep to the grass: never in a pond, a rock or a tree, and never parked on a start pad or the chest.
game = fresh(7, dressed=True)
for step in range(60 * 40):
    game.update_cows(1 / 60)
    if step % 30 == 0:
        for cow in game.cows:
            assert not game.in_water(cow.pos) and game.blocked_at(cow.pos) is None
            assert all(cow.pos.distance_to(pad) > 40 for pad in [runner.start for runner in game.runners.values()] + [game.chest])
assert any(cow.pos.distance_to(cow.target) > 1 or cow.wait > 0 for cow in game.cows)

# Solo: one beaver crosses the whole board, edge to edge, on the clock, on every board, the lopsided ones included.
for index in range(len(BOARDS) + len(SOLO_BOARDS)):
    game = Treasure(config)
    game.new_match((1,), (1,), index, solo=True)
    assert game.rock_at(game.chest, 40) is None and not game.in_water(game.chest) and game.blocked_at(game.runners[1].start, 30) is None
    assert len(game.runners) == 1 and game.chest.x > game.width * .85 and game.runners[1].start.x < game.width * .15
    elapsed = 0.0
    while game.phase != "match_over" and elapsed < 120:
        game.update(1 / 60, {})
        elapsed += 1 / 60
    assert game.winner == 1 and game.verdict == f"{game.race_time:.2f} s" and 5 < game.race_time < 25, (game.board["name"], game.verdict)
game = Treasure(config)
game.new_match((1,), (), 0, solo=True)
run(game, config["treasure"]["draw_seconds"] + config["treasure"]["check_seconds"] + .3)
assert game.phase == "match_over" and game.winner is None and game.verdict == "BROKEN PATH"

# Today's leaderboard: ranked per board, kept across restarts, and wiped when the date changes.
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "board.json"
    board = Leaderboard(path, today="2026-09-19")
    assert board.record("TWIN LAKES", "ALEX", 11.84) == 1 and board.record("TWIN LAKES", "JO", 9.97) == 1
    assert board.record("TWIN LAKES", "SAM", 13.2) == 3 and board.record("THE MOAT", "SAM", 20) == 1
    assert [name for name, seconds in Leaderboard(path, today="2026-09-19").top("TWIN LAKES")] == ["JO", "ALEX", "SAM"]
    assert Leaderboard(path, today="2026-09-20").top("TWIN LAKES") == []
    path.write_text("not json")
    assert Leaderboard(path, today="2026-09-19").top("TWIN LAKES") == []

# Rendering every phase works, and every sound the game names exists.
surface = pygame.Surface((1280, 720))
for bots in ((2,), (1, 2)):
    game = fresh(3, bots)
    for seconds in (1, 6, 3, 10):
        run(game, seconds)
        game.draw(surface)
names = {word for line in open("beaver_battle/treasure.py") if "sounds.append(" in line
         for word in __import__("re").findall(r'"(\w+)"', line.split("sounds.append(", 1)[1])} - {"swim_speed", "walk_speed"}
assert names <= set(sound.build()), names - set(sound.build())

print(f"Treasure checks passed: {len(BOARDS)} fair mirrored boards and {len(SOLO_BOARDS)} lopsided one-player boards, gap merging, broken paths, "
      "rocks, trees, logs, portals, cows, boost tokens, mirrored scatter, tracing, water, boost, solo, leaderboard, "
      f"bot races averaging {sum(times) / len(times):.0f} s, rendering, sounds")
