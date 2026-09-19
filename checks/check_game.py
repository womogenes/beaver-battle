"""Run with: .venv/bin/python -m checks.check_game [optional-render.png]."""

import copy
import math
import os
import sys
import tomllib

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from beaver_battle.game import Game, Mine, Pickup, Prop, Rock
from beaver_battle.model import PlayerInput


pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)


def arena(players=(1, 2), moving=False):
    settings = copy.deepcopy(config)
    if not moving:
        settings["game"]["canoe_speed"] = 0
    game = Game(settings)
    game.new_match(players)
    game.props.clear()
    for index, player in enumerate(game.players.values()):
        player.pos = pygame.Vector2(200 + index * 200, 350)
        player.heading = 0
        player.invulnerability = 0
    return game


game = Game(config)
game.new_match([1, 2, 3])
assert not game.blocked
assert {prop.kind for prop in game.props} == {"wall", "barrier", "asteroid", "barrel", "turret", "beam"}

# Continuous motion, a bounded turn rate, and no aim means retaining heading.
game = arena((1,), moving=True)
player = game.players[1]
start = player.pos.copy()
game.update(.1, {1: PlayerInput(1, aim=tuple(start))})
assert player.pos.x > start.x and abs(player.heading) < .001
game.update(.1, {1: PlayerInput(1, aim=(player.pos.x, 650))})
assert 0 < player.heading <= math.radians(config["game"]["turn_speed"] * .1) + 1e-6
heading = player.heading
game.update(.1, {1: PlayerInput(1, aim=(float("nan"), 0))})
assert abs(player.heading - heading) < 1e-6

# A held trigger spends exactly three rounds, then blocks all new activations.
game = arena((1,))
control = {1: PlayerInput(1, fire=True)}
game.update(0, control)
game.update(config["game"]["shot_interval"], control)
game.update(config["game"]["shot_interval"], control)
assert game.players[1].ammo == 0 and game.players[1].reload > 2.4 and len(game.rocks) == 3
game.players[1].powerup = "laser"
game.update(1, {1: PlayerInput(1, fire=True, special=True)})
assert game.players[1].ammo == 0 and game.players[1].powerup == "laser"
game.update(1.6, {})
assert game.players[1].reload == 0 and game.players[1].ammo == 3

# One projectile cannot take both lives; feedback identifiers remain unique.
game = arena()
victim = game.players[2]
assert game.hit(victim) and victim.state == "beaver"
assert not game.hit(victim) and len(game.events) == 1
first_event = game.events[0].event_id
game.update(.81, {})
assert game.hit(victim) and victim.state == "eliminated"
assert game.events[-1].event_id > first_event
game.update(0, {})
assert game.phase == "round_over" and game.scores[1] == 1
game.update(0, {})
assert game.scores[1] == 1

# First-to-five wins and automatic round reset; ties award no point.
game = arena()
for score in range(1, 6):
    game.players[2].state = "eliminated"
    game.update(0, {})
    assert game.scores[1] == score
    if score < 5:
        assert game.phase == "round_over"
        game.update(config["game"]["round_delay"] + .02, {})
        game.props.clear()
        assert game.phase == "playing" and all(player.state == "canoe" for player in game.players.values())
assert game.phase == "match_over"
game = arena()
for player in game.players.values():
    player.state = "eliminated"
game.update(0, {})
assert game.winner is None and not any(game.scores.values())

# Thin physical walls stop fast rocks, muzzle spawns, and laser rays.
game = arena()
walls = np.zeros((720, 1280), dtype=bool)
walls[:, 300] = True
game.update(0, {}, walls)
game.rocks.append(Rock(1, pygame.Vector2(250, 350), pygame.Vector2(20000, 0), 4, 2))
game.update(1 / 60, {})
assert not game.rocks and game.players[2].state == "canoe"
game.players[1].powerup = "laser"
game.update(0, {1: PlayerInput(1, special=True)})
assert game.players[2].state == "canoe"
game.players[1].pos = pygame.Vector2(281, 350)
game.shoot(1, game.players[1].pos, 0, 18)
assert not game.rocks

# Live ink safely relocates players and pickups, never damages them, and can resume.
game = arena()
walls = np.zeros((720, 1280), dtype=bool)
walls[280:420, 130:270] = True
game.mines.append(Mine(1, pygame.Vector2(200, 350), 9))
game.update(0, {}, walls)
assert game.physical_free(game.players[1].pos, game.players[1].radius)
assert game.players[1].state == "canoe" and not game.events and not game.mines
game.update(0, {}, np.ones_like(walls))
assert game.blocked and game.error
game.update(0, {}, np.zeros_like(walls))
assert not game.blocked and not game.error

# Wall impacts preserve lives and reflect the moving canoe.
game = arena((1,), moving=True)
game.players[1].pos = pygame.Vector2(260, 350)
walls = np.zeros((720, 1280), dtype=bool)
walls[:, 300] = True
game.update(.4, {}, walls)
assert game.players[1].pos.x < 282 and game.players[1].state == "canoe" and not game.events

# Destroying barrels/asteroids yields all three randomly ordered weapon types.
game = arena((1,))
game.props = [Prop(kind, pygame.Vector2(400 + 100 * index, 350), 20, 1)
              for index, kind in enumerate(("barrel", "asteroid", "barrel"))]
for prop in game.props:
    game.hit(prop, 3)
assert {pickup.kind for pickup in game.pickups} == {"laser", "jouster", "mine"}
assert all(prop.hp <= 0 for prop in game.props)
game.update(0, {}, np.zeros((720, 1280), dtype=bool))
assert all(prop.hp <= 0 for prop in game.props), "Live camera updates must not restore destroyed objects"

# Pickups drift and bounce; collecting and activating use separate actions.
game = arena((1,))
walls = np.zeros((720, 1280), dtype=bool)
walls[:, 300] = True
game.pickups = [Pickup("laser", pygame.Vector2(280, 200), pygame.Vector2(100, 0), 10)]
game.update(.5, {}, walls)
assert game.pickups[0].pos.x < 290
game.pickups[0].pos = game.players[1].pos.copy()
game.pickups[0].velocity.update(0, 0)
game.update(0, {})
assert game.players[1].powerup == "laser" and not game.pickups

# A laser hits once; button 2 must be released before the next activation.
game = arena()
game.players[1].powerup = "laser"
game.update(0, {1: PlayerInput(1, special=True)})
assert game.players[2].state == "beaver" and game.players[1].powerup is None
game.players[1].powerup = "mine"
game.update(0, {1: PlayerInput(1, special=True)})
assert game.players[1].powerup == "mine" and not game.mines
game.update(0, {})
game.update(0, {1: PlayerInput(1, special=True)})
assert len(game.mines) == 1 and game.players[1].powerup is None

# Jousters boost speed and their lance damages a close opponent.
game = arena(moving=True)
game.players[1].powerup = "jouster"
game.update(0, {1: PlayerInput(1, special=True)})
start = game.players[1].pos.copy()
game.update(.1, {})
assert game.players[1].pos.distance_to(start) > config["game"]["canoe_speed"] * .1
game.players[2].pos = game.players[1].pos + pygame.Vector2(40, 0)
game.update(0, {})
assert game.players[2].state == "beaver" and game.players[1].joust == 0

# Mines arm, damage opponents, spare their owner, and respect physical cover.
game = arena()
game.players[1].pos = pygame.Vector2(400, 350)
game.players[2].pos = pygame.Vector2(450, 350)
game.mines = [Mine(1, pygame.Vector2(400, 350), 9)]
events = game.update(.61, {})
assert game.players[1].state == "canoe" and game.players[2].state == "beaver" and len(events) == 1
game = arena()
game.players[2].pos = pygame.Vector2(550, 350)
game.mines = [Mine(1, pygame.Vector2(500, 350), 9)]
walls = np.zeros((720, 1280), dtype=bool)
walls[:, 525] = True
game.update(.7, {}, walls)
assert game.players[2].state == "canoe" and game.mines

# Turret fire damages a target, without colliding with its own emitter.
game = arena()
game.players[1].pos = pygame.Vector2(200, 600)
game.players[2].pos = pygame.Vector2(450, 200)
turret = Prop("turret", pygame.Vector2(200, 200), 20, 3, cooldown=0)
game.props = [turret]
game.update(.5, {})
assert turret.hp == 3 and game.players[2].state == "beaver"

# Beams warn first, then damage each player at most once per activation.
game = arena()
game.players[1].pos = pygame.Vector2(200, 600)
game.players[2].pos = pygame.Vector2(450, 200)
game.props = [Prop("beam", pygame.Vector2(200, 200), 18, 3)]
game.update(.95, {})
assert game.players[2].state == "canoe"
game.update(.2, {})
assert game.players[2].state == "beaver"
game.players[2].invulnerability = 0
game.update(.1, {})
assert game.players[2].state == "beaver"

# Beaver thrust changes speed while keeping a nonzero floating speed.
game = arena((1,))
game.hit(game.players[1])
game.update(.3, {1: PlayerInput(1, fire=True)})
assert game.players[1].speed == config["game"]["beaver_boost_speed"]
game.update(.3, {})
assert game.players[1].speed == config["game"]["beaver_speed"]

# The complete renderer does not project dark ink or laser-red placeholder pixels.
game = Game(config)
game.new_match([1, 2, 3])
game.update(.1, {})
surface = pygame.Surface((1280, 720))
game.draw(surface)
pixels = pygame.surfarray.array3d(surface)
assert pixels.max(axis=2).min() > config["camera"]["wall_threshold"]
redness = pixels[:, :, 0].astype(np.int16) - pixels[:, :, 1:].max(axis=2).astype(np.int16)
assert not np.any((pixels[:, :, 0] >= 160) & (redness >= 60))
if len(sys.argv) > 1:
    pygame.image.save(surface, sys.argv[1])
pygame.quit()
print("Game checks passed: movement, ammo, lives, feedback, rounds, live walls, every weapon and hazard, rendering")
