"""Run with: .venv/bin/python -m checks.check_game [optional-render.png]."""

import copy
import math
import os
import sys
import tomllib

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import cv2
import numpy as np
import pygame

from beaver_battle.game import Game, Mine, Pickup, Prop, Rock, Shape, closed_shapes
from beaver_battle.model import PlayerInput


pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)
# These checks describe the original rule set; checks/check_rules.py covers the Astro Party one.
config["game"].update(reload_mode="magazine", canoe_return=0, scoring="survivor", ram_swimmers=False)


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
game.new_match([1, 2])
assert not game.blocked
assert {prop.kind for prop in game.props} == {"asteroid"}

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

# Laser hold never shoots; each second-button press gives one action, never autofire.
game = arena((1,))
control = {1: PlayerInput(1, fire=True)}
game.update(0, control)
game.update(.5, control)
assert not game.rocks and game.players[1].ammo == 3
control[1].special = True
game.update(0, control)
game.update(.35, control)
assert len(game.rocks) == 1 and game.players[1].ammo == 2
for shot in range(2):
    game.update(config["game"]["shot_interval"] + .02, {})
    game.update(0, {1: PlayerInput(1, special=True)})
assert game.players[1].ammo == 0 and game.players[1].reload > 2.4
game.players[1].powerup = "laser"
game.update(1, {1: PlayerInput(1, fire=True, special=True)})
assert game.players[1].ammo == 0 and game.players[1].powerup == "laser"
game.update(1.6, {})
assert game.players[1].reload == 0 and game.players[1].ammo == 3
# A quick tap retained by the network is consumed just once, for either player.
for player_id in (1, 2):
    game = arena((player_id,))
    tap = {player_id: PlayerInput(player_id, special_pressed=True)}
    game.update(0, tap)
    game.update(.4, tap)
    assert len(game.rocks) == 1

# One projectile cannot take both lives; feedback identifiers remain unique.
game = arena()
game.config["feedback"] = {"duration_ms": 175}
victim = game.players[2]
assert game.hit(victim) and victim.state == "beaver"
assert not game.hit(victim) and len(game.events) == 1
assert (game.events[0].player_id, game.events[0].kind, game.events[0].duration_ms) == (2, "hit", 175)
first_event = game.events[0].event_id
game.update(.81, {})
assert not game.events, "The next game update must not replay a prior squeeze"
assert game.hit(victim) and victim.state == "eliminated"
assert len(game.events) == 1 and game.events[0].player_id == 2
assert game.events[-1].event_id > first_event
victim.invulnerability = 0
assert not game.hit(victim) and len(game.events) == 1, "Eliminated players must not emit another squeeze"
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
assert not game.events, "A rock blocked by ink must not squeeze the protected player"
game.players[1].powerup = "laser"
game.update(0, {1: PlayerInput(1, special=True)})
assert game.players[2].state == "canoe"
assert not game.events, "A laser blocked by ink must not squeeze the protected player"
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

# Closed ink outlines become solid logs or rocks; open, edge-bounded, tiny, and arena-sized ones stay hollow.
def drawing(*marks):
    ink = np.zeros((720, 1280), np.uint8)
    for mark in marks:
        mark(ink)
    return ink.astype(bool)


def log_outline(ink, center=(400, 420), degrees=25):
    cv2.polylines(ink, [cv2.boxPoints((center, (300, 70), degrees)).astype(np.int32)], True, 1, 6)


def rock_outline(ink):
    cv2.circle(ink, (850, 300), 70, 1, 6)


walls = drawing(log_outline, rock_outline,
                lambda ink: cv2.ellipse(ink, (1000, 560), (80, 60), 0, 40, 320, 1, 6),
                lambda ink: cv2.ellipse(ink, (640, 719), (90, 70), 0, 180, 360, 1, 6),
                lambda ink: cv2.circle(ink, (150, 600), 8, 1, 3),
                lambda ink: cv2.rectangle(ink, (30, 80), (1250, 640), 1, 6))
solid, shapes = closed_shapes(walls, 5, 400, .25 * walls.size)
assert sorted(shape.kind for shape in shapes) == ["log", "rock"]
log = next(shape for shape in shapes if shape.kind == "log")
assert abs(math.degrees(log.angle) - 25) < 2
assert solid[420, 400] and solid[300, 850] and not walls[420, 400] and not walls[300, 850]
assert not solid[560, 1000] and not solid[690, 640] and not solid[600, 150] and not solid[200, 640]
assert np.array_equal(solid & walls, walls)

# Fills follow the live mask: a hairline break is bridged, erasing a real gap reopens the water, redrawing refills.
game = arena()
game.players[1].pos = pygame.Vector2(400, 420)
game.update(0, {}, drawing(log_outline))
assert [shape.kind for shape in game.shapes] == ["log"] and not game.blocked
assert not game.physical_free(pygame.Vector2(400, 420), 4)
assert game.physical_free(game.players[1].pos, game.players[1].radius) and game.players[1].state == "canoe"
assert not game.walls[int(game.players[1].pos.y), int(game.players[1].pos.x)]
end, target = game.trace(pygame.Vector2(100, 420), pygame.Vector2(700, 420), 4, players=False)
assert target == "wall" and end.x < 400
hairline = drawing(rock_outline)
hairline[298:301, 900:940] = False
game.update(0, {}, hairline)
assert [shape.kind for shape in game.shapes] == ["rock"] and not game.physical_free(pygame.Vector2(850, 300), 4)
# Filling now tolerates a gap up to game.shape_closure of a shape's own width, so what
# counts as erased is proportional too: a marker-width nick no longer reopens a fill, and
# a doorway has to be a real one. That is the cost of bridging pen lifts and dry dashes.
erased = drawing(rock_outline)
erased[190:410, 850:940] = False
game.update(0, {}, erased)
assert not game.shapes and game.physical_free(pygame.Vector2(850, 300), 4)
game.update(0, {}, drawing(rock_outline))
assert len(game.shapes) == 1 and not game.physical_free(pygame.Vector2(850, 300), 4)

# Camera jitter neither wobbles the grain nor flips a borderline fill between log and rock.
game = arena()
game.update(0, {}, drawing(log_outline))
angle = game.shapes[0].angle
game.update(0, {}, drawing(lambda ink: log_outline(ink, (402, 419), 28)))
assert game.shapes[0].angle == angle
game.update(0, {}, drawing(lambda ink: log_outline(ink, (402, 419), 60)))
assert abs(math.degrees(game.shapes[0].angle) - 60) < 2
game.update(0, {}, drawing(lambda ink: cv2.rectangle(ink, (600, 300), (810, 400), 1, 6)))
assert game.shapes[0].kind == "log" and 2 < game.shapes[0].ratio < 2.3
game.update(0, {}, drawing(lambda ink: cv2.rectangle(ink, (600, 300), (790, 400), 1, 6)))
assert game.shapes[0].kind == "log" and game.shapes[0].ratio < 2
game.update(0, {}, np.zeros((720, 1280), dtype=bool))
game.update(0, {}, drawing(lambda ink: cv2.rectangle(ink, (600, 300), (790, 400), 1, 6)))
assert game.shapes[0].kind == "rock"

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
assert not game.events, "Damaging scenery must not squeeze a player's controller"
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
assert len(game.events) == 1 and game.events[0].player_id == 2
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
assert len(game.events) == 1 and game.events[0].player_id == 2

# Mines arm, damage opponents, spare their owner, and respect physical cover.
game = arena()
game.players[1].pos = pygame.Vector2(400, 350)
game.players[2].pos = pygame.Vector2(450, 350)
game.mines = [Mine(1, pygame.Vector2(400, 350), 9)]
events = game.update(.61, {})
assert game.players[1].state == "canoe" and game.players[2].state == "beaver" and len(events) == 1
assert events[0].player_id == 2
game = arena()
game.players[2].pos = pygame.Vector2(550, 350)
game.mines = [Mine(1, pygame.Vector2(500, 350), 9)]
walls = np.zeros((720, 1280), dtype=bool)
walls[:, 525] = True
game.update(.7, {}, walls)
assert game.players[2].state == "canoe" and game.mines
assert not game.events, "A mine blocked by cover must not squeeze the protected player"

# Turret fire damages a target, without colliding with its own emitter.
game = arena()
game.players[1].pos = pygame.Vector2(200, 600)
game.players[2].pos = pygame.Vector2(450, 200)
turret = Prop("turret", pygame.Vector2(200, 200), 20, 3, cooldown=0)
game.props = [turret]
game.update(.5, {})
assert turret.hp == 3 and game.players[2].state == "beaver"
assert len(game.events) == 1 and game.events[0].player_id == 2

# Beams warn first, then damage each player at most once per activation.
game = arena()
game.players[1].pos = pygame.Vector2(200, 600)
game.players[2].pos = pygame.Vector2(450, 200)
game.props = [Prop("beam", pygame.Vector2(200, 200), 18, 3)]
game.update(.95, {})
assert game.players[2].state == "canoe"
assert not game.events, "A beam warning is not a damaging hit"
game.update(.2, {})
assert game.players[2].state == "beaver"
assert len(game.events) == 1 and game.events[0].player_id == 2
game.players[2].invulnerability = 0
game.update(.1, {})
assert game.players[2].state == "beaver"
assert not game.events, "A beam must not repeat feedback during the same activation"

# Beaver thrust changes speed while keeping a nonzero floating speed.
game = arena((1,))
game.hit(game.players[1])
game.update(.3, {1: PlayerInput(1, fire=True)})
assert game.players[1].speed == config["game"]["beaver_boost_speed"]
game.update(.3, {})
assert game.players[1].speed == config["game"]["beaver_speed"]

# The complete renderer does not project dark ink or laser-red placeholder pixels.
game = Game(config)
game.new_match([1, 2])
game.update(.1, {}, drawing(log_outline, rock_outline))
surface = pygame.Surface((1280, 720))
game.draw(surface)
pixels = pygame.surfarray.array3d(surface)
assert len({tuple(color) for color in pixels[330:510:3, 420]}) > 1 and len({tuple(color) for color in pixels[800:900:3, 300]}) > 1
assert pixels.max(axis=2).min() > config["camera"]["wall_threshold"]
redness = pixels[:, :, 0].astype(np.int16) - pixels[:, :, 1:].max(axis=2).astype(np.int16)
assert not np.any((pixels[:, :, 0] >= 160) & (redness >= 60))
if len(sys.argv) > 1:
    pygame.image.save(surface, sys.argv[1])
# The responsive preset must turn to a fresh laser aim in one physics step,
# including reversing direction, without retaining rotational interpolation.
responsive = arena((1,))
responsive.config['game']['turn_speed'] = 10800
pilot = responsive.players[1]
for offset in ((-100, 0), (0, -100), (100, 0), (0, 100)):
    aim = (pilot.pos.x + offset[0], pilot.pos.y + offset[1])
    responsive.update(1 / 60, {1: PlayerInput(1, aim=aim)})
    wanted = math.atan2(offset[1], offset[0])
    assert abs((pilot.heading - wanted + math.pi) % math.tau - math.pi) < 1e-6
print('Responsive heading check passed: reversals finish in one physics step')

pygame.quit()
print("Game checks passed: movement, ammo, lives, feedback, rounds, live walls, closed-shape fills, every weapon and hazard, rendering")

# Restored shape contours may extend one pixel beyond any image edge.
edge_game = arena()
edge_ink = np.zeros((720, 1280), dtype=bool)
edge_ink[100:104, 1180:1280] = True
outside = np.array([[1280, 100], [-1, 100], [500, -1], [500, 720]], dtype=np.int32).reshape(-1, 1, 2)
edge_shape = Shape('rock', outside, (640, 360), 0, 1)
sticks, loose = edge_game.find_sticks(edge_ink, [edge_shape])
assert len(sticks) == 1, 'Off-board negative points must not label ink on the opposite edge'
assert np.array_equal(loose, edge_ink)
for left, top, right, bottom in ((0, 200, 150, 350), (1129, 200, 1279, 350),
                                  (500, 0, 650, 150), (500, 569, 650, 719)):
    board = np.zeros((720, 1280), np.uint8)
    cv2.rectangle(board, (left, top), (right, bottom), 1, 3)
    edge_game.update(1 / 60, {}, board.astype(bool))
print('Live geometry accepts contours beyond all four camera edges without wrapping or crashing')
