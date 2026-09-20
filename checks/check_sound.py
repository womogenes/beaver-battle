"""Sound effects exist for everything the game announces, and the game announces the right things."""

import copy
import math
import os
import re
import tomllib
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from beaver_battle import sound
from beaver_battle.game import Game
from beaver_battle.model import PlayerInput

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)

# Every effect is finite, audible, short, and never clips before scaling.
effects = sound.build()
for name, wave in effects.items():
    assert np.isfinite(wave).all() and .2 < np.abs(wave).max(), name
    assert .02 < len(wave) / sound.RATE < 2.5, name

# Every name game.py can emit has an effect, so nothing happens in silence.
source = Path("beaver_battle/game.py").read_text()
emitted = {name for line in source.splitlines() if "sounds.append(" in line for name in re.findall(r'"(\w+)"', line.split("sounds.append(", 1)[1])}
emitted -= {"beaver", "match_over"}
assert {"shoot", "hit", "splash", "pickup", "laser", "powerup", "ram", "drop", "boom"} <= emitted
assert emitted <= set(effects), emitted - set(effects)

# A silent board never fails; a real one loads every effect even on the dummy audio driver.
sound.SoundBoard(False).play(["shoot", "missing"])
board = sound.SoundBoard(True)
assert not board.sounds or set(board.sounds) == set(effects)
board.play(["shoot", "splash", "missing"])

# Shooting is heard; the first hit bonks and the second one splashes.
settings = copy.deepcopy(config)
settings["game"]["canoe_speed"] = 0
game = Game(settings)
game.new_match([1, 2])
game.props.clear()
for index, player in enumerate(game.players.values()):
    player.pos, player.heading, player.invulnerability = pygame.Vector2(300 + index * 200, 360), 0, 0
# One press of the second button throws one rock; the first button only aims.
game.update(1 / 60, {1: PlayerInput(1, (900, 360), True, True), 2: PlayerInput(2, (900, 360), False, False)})
assert "shoot" in game.sounds
game.sounds.clear()
target = game.players[2]
game.hit(target)
assert game.sounds == ["hit"] and target.state == "beaver"
target.invulnerability = 0
game.hit(target)
assert game.sounds[:3] == ["hit", "splash", "whoosh"] and target.state == "eliminated"

# Undrained sounds stay bounded.
game.sounds.extend(["bump"] * 500)
game.update(1 / 60, {})
assert len(game.sounds) <= 40

print("Sound checks passed: every effect synthesised, every game event voiced, shoot/hit/splash order, silent fallback")
