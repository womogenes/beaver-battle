"""Checks for the computer opponents: PYTHONPATH=. SDL_VIDEODRIVER=dummy python -m checks.check_bots"""

import copy
import os
import tomllib

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from beaver_battle.app import simulated_walls
from beaver_battle.bots import BattleBot, DamAttacker, DamBot
from beaver_battle.dam import MAPS, DamIt
from beaver_battle.game import Game
from beaver_battle.model import PlayerInput

pygame.init()
pygame.display.set_mode((1, 1))
with open("config.toml", "rb") as source:
    config = tomllib.load(source)

# Beaver Battle. Against a canoe that just sits there the bot must find it, line up and win, whichever controller
# number it holds, and never sit wedged for long. It plays through PlayerInput alone: one tap of button 2 per rock.
for bot_id, seed in ((2, 1), (1, 2)):
    game = Game(copy.deepcopy(config))
    game.new_match([1, 2], simulated_walls(1280, 720))
    bot, other, clock, still, longest, last, taps, held = BattleBot(bot_id, seed), 3 - bot_id, 0.0, 0.0, 0.0, None, 0, 0
    while game.phase != "match_over" and clock < 120:
        move = bot.step(game, 1 / 60)
        assert move.player_id == bot_id and not move.fire or game.players[bot_id].state == "beaver", "button 1 only sprints a swimmer"
        taps += move.special_pressed
        frozen = game.held(1 / 60)  # A hit stops the clock for a moment; nobody moves then, and that is not being stuck.
        if not frozen:
            game.update(1 / 60, {other: PlayerInput(other, (0, 0), False, False), bot_id: move})
        me = game.players[bot_id]
        still = still + 1 / 60 if last is not None and not frozen and me.pos.distance_to(last) < .3 and game.phase == "playing" and me.state == "canoe" else 0.0 if not frozen else still
        longest, last, clock = max(longest, still), me.pos.copy(), clock + 1 / 60
    assert game.phase == "match_over" and game.scores[bot_id] > game.scores[other], (bot_id, game.scores, clock)
    assert longest < 4 and taps >= 10, (longest, taps)
# Two bots make a match of it: both score.
game = Game(copy.deepcopy(config))
game.new_match([1, 2], simulated_walls(1280, 720))
pair, clock = {1: BattleBot(1, 3), 2: BattleBot(2, 4)}, 0.0
while game.phase != "match_over" and clock < 400:
    moves = {player_id: bot.step(game, 1 / 60) for player_id, bot in pair.items()}
    if not game.held(1 / 60):
        game.update(1 / 60, moves)
    clock += 1 / 60
assert min(game.scores.values()) >= 1 and max(game.scores.values()) >= 3, game.scores

# Dam It!. The bot does whichever job the round gives it: as builder it draws, in light, a dam that holds water on
# every map, even when a camera is reporting (empty) whiteboard ink; as attacker it gets through a middling dam.
blank = None
game = DamIt(copy.deepcopy(config))
game.bots = {1, 2}
pair = {1: DamBot(1), 2: DamBot(2)}
for round_number in range(2 * len(MAPS)):
    game.new_match((1, 2))
    blank = blank if blank is not None else game.ink.copy()
    clock, reached = 0.0, set()
    while game.phase != "match_over" and clock < 120:
        moves = {player_id: move for player_id, bot in pair.items() if (move := bot.step(game, 1 / 60)) is not None}
        game.update(1 / 60, moves, blank)
        reached.add(game.phase)
        clock += 1 / 60
    assert "attack" in reached, f"{MAPS[game.board_index]['name']}: the bot's dam leaked ({game.verdict})"
    assert game.phase == "match_over" and game.wood_length(game.ink) > 200
# The playable attacker is beatable: the best dam on the first map holds it off, where the careful one gets through.
for clumsy, floods in ((True, False), (False, True)):
    game = DamIt(copy.deepcopy(config))
    game.new_match((1, 2), swap=False, board=0)
    game.scribble(pygame.Vector2(170, 0), pygame.Vector2(170, game.height))
    game.timer = 0
    bot = DamAttacker(2, 2.0, 2, 1.2) if clumsy else DamAttacker(2, 1.2, 5)
    while game.phase != "match_over":
        move = bot.step(game, 1 / 60) if game.phase == "attack" else None
        game.update(1 / 60, {} if move is None else {2: move})
    assert (game.winner == 2) == floods, (clumsy, game.verdict)

print("Bot checks passed: the canoe bot hunts, shoots by taps, frees itself and wins from either controller number; two bots "
      "trade rounds; the dam bot builds a holding dam on every map in light and chews through one; the playable attacker loses to the best dam")
