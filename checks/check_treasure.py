"""Treasure Dash: every board is a fair puzzle, ink becomes a route, and the race plays out by its rules."""

import math
import tempfile
import tomllib
from pathlib import Path

import numpy as np
import pygame

from beaver_battle import sound
from beaver_battle.leaderboard import Leaderboard
from beaver_battle.model import PlayerInput
from beaver_battle.treasure import BOARDS, Treasure, cheapest_route

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)


def fresh(board, bots=()):
    game = Treasure(config)
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
    game = fresh(index)
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
    game = fresh(index, bots=(1, 2))
    elapsed = 0.0
    while game.phase != "match_over" and elapsed < 200:
        game.update(1 / 60, {})
        elapsed += 1 / 60
        if game.phase == "running" and not times[index:]:
            times.append(-elapsed)
    assert game.phase == "match_over" and game.verdict == "TREASURE!", (BOARDS[index]["name"], game.verdict)
    times[index] += elapsed
assert 5 < sum(times) / len(times) < 14, times

# Solo: one beaver crosses the whole board, edge to edge, on the clock, on every board.
for index in range(len(BOARDS)):
    game = Treasure(config)
    game.new_match((1,), (1,), index, solo=True)
    assert len(game.runners) == 1 and game.chest.x > game.width * .85 and game.runners[1].start.x < game.width * .15
    elapsed = 0.0
    while game.phase != "match_over" and elapsed < 120:
        game.update(1 / 60, {})
        elapsed += 1 / 60
    assert game.winner == 1 and game.verdict == f"{game.race_time:.2f} s" and 5 < game.race_time < 20, (BOARDS[index]["name"], game.verdict)
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

print(f"Treasure checks passed: {len(BOARDS)} fair mirrored boards, gap merging, broken paths, rocks, tracing, water, boost, solo, leaderboard, "
      f"bot races averaging {sum(times) / len(times):.0f} s, rendering, sounds")
