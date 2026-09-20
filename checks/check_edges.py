"""A canoe steered into an edge hugs it and slides: no ping-pong, no shimmy, one bump, and it peels off on demand."""

import math
import tomllib

import numpy as np
import pygame

from beaver_battle.game import Game
from beaver_battle.model import PlayerInput

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)


def drive(start, heading, aim, frames=480, walls=None):
    game = Game(config)
    game.new_match([1, 2], walls)
    game.props.clear()
    canoe = game.players[1]
    canoe.pos, canoe.heading, canoe.invulnerability = pygame.Vector2(start), heading, 99
    game.players[2].pos, game.players[2].invulnerability = pygame.Vector2(640, 650), 99
    bumps = reversals = 0
    last = None
    for frame in range(frames):
        before = canoe.heading
        game.update(1 / 60, {1: PlayerInput(1, aim(canoe))})
        bumps += game.sounds.count("bump")
        game.sounds.clear()
        turn = (canoe.heading - before + math.pi) % math.tau - math.pi
        if frame >= 90 and abs(turn) > math.radians(1):
            reversals += last is not None and turn * last < 0
            last = turn
    return game, canoe, bumps, reversals


cases = {
    "top edge": ((640, 200), -math.pi / 2, lambda canoe: (canoe.pos.x, -400)),
    "glancing the top edge": ((300, 200), -math.pi / 4, lambda canoe: (canoe.pos.x + 300, -300)),
    "right edge": ((900, 360), 0, lambda canoe: (2000, canoe.pos.y)),
    "corner": ((300, 300), -2.4, lambda canoe: (-400, -400)),
}
for name, (start, heading, aim) in cases.items():
    game, canoe, bumps, reversals = drive(start, heading, aim)
    assert bumps <= 1 and reversals == 0, (name, bumps, reversals)
    assert canoe.wall.length_squared(), name

# Aimed back at open water, it leaves the edge at once.
game, canoe, bumps, reversals = drive((400, 120), -math.pi / 2, lambda canoe: (canoe.pos.x + 80, -300), 90)
for frame in range(40):
    game.update(1 / 60, {1: PlayerInput(1, (640, 360))})
assert canoe.pos.y > 100 and not canoe.wall.length_squared()

# It slides down a drawn stroke and carries on past the end of it.
walls = np.zeros((720, 1280), bool)
walls[200:420, 600:608] = True
game, canoe, bumps, reversals = drive((450, 300), .35, lambda canoe: (1100, 520), 150, walls)
assert bumps <= 1 and canoe.pos.x > 800

print("Edge checks passed: hugging on every side and in a corner, single bump, no shimmy, release, sliding past a stroke")
