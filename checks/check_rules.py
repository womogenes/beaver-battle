"""The Astro Party rule set: rocks return one at a time, a swimmer gets a new canoe, ramming sinks
swimmers, and points go to whoever does the sinking."""

import copy
import tomllib

import pygame

from beaver_battle.game import Game
from beaver_battle.model import PlayerInput

pygame.init()
with open("config.toml", "rb") as source:
    config = tomllib.load(source)
rules = config["game"]
assert (rules["reload_mode"], rules["scoring"], rules["ram_swimmers"]) == ("each", "kills", True) and rules["canoe_return"] > 0


def arena(players=(1, 2), **overrides):
    settings = copy.deepcopy(config)
    settings["game"].update(canoe_speed=0, beaver_speed=0, beaver_boost_speed=0, **overrides)
    game = Game(settings)
    game.new_match(players)
    game.props.clear()
    for index, player in enumerate(game.players.values()):
        player.pos, player.heading, player.invulnerability = pygame.Vector2(200 + index * 300, 360), 0, 0
    return game


def run(game, seconds, inputs=None):
    for step in range(round(seconds * 60)):
        game.update(1 / 60, inputs or {})


# Rocks come back one at a time, and the canoe can fire again as soon as it has one.
game = arena()
game.players[2].pos.update(200, 650)
hold = {1: PlayerInput(1, special=True)}
for shot in range(3):
    game.update(0, {})
    game.update(0, hold)
    if shot < 2: run(game, rules["shot_interval"] + .02)
shooter = game.players[1]
assert shooter.ammo == 0 and len(game.rocks) == 3
run(game, rules["reload_each"] - rules["shot_interval"] * 2 + .05)
game.update(0, hold)
assert len(game.rocks) >= 4, "a fourth rock should leave as soon as one has come back"
run(game, rules["reload_each"] * 3 + .2)
assert shooter.ammo == rules["magazine"] and shooter.reload == 0

# A power-up can be used while rocks are still coming back.
game = arena()
game.players[1].powerup, game.players[1].ammo, game.players[1].reload = "jouster", 1, .5
game.update(1 / 60, {1: PlayerInput(1, special=True)})
assert game.players[1].joust > 0

# A swimmer who survives gets a fresh canoe: full rocks, a moment of grace, and only then can be hit again.
game = arena()
victim = game.players[2]
assert game.hit(victim, by=1) and victim.state == "beaver" and game.scores == {1: 0, 2: 0}
run(game, rules["canoe_return"] - .2)
assert victim.state == "beaver"
run(game, .4)
assert victim.state == "canoe" and victim.ammo == rules["magazine"] and victim.invulnerability > 1
assert "return" in game.sounds and not game.hit(victim, by=1)
run(game, rules["return_invulnerability"] + .1)
assert game.hit(victim, by=1) and victim.state == "beaver"

# Sinking a swimmer scores for whoever did it, not for being the last canoe afloat.
game = arena()
target = game.players[2]
game.hit(target, by=1)
assert game.scores == {1: 0, 2: 0}, "knocking a beaver into the water is not a point yet"
target.invulnerability = 0
game.hit(target, by=1)
game.update(0, {})
assert target.state == "eliminated" and game.scores == {1: 1, 2: 0} and game.phase == "round_over" and game.winner == 1

# Nobody scores for a sinking with no culprit, or for sinking themselves.
game = arena()
victim = game.players[2]
for culprit in (None, 2):
    victim.state, victim.invulnerability = "beaver", 0
    game.hit(victim, by=culprit)
    assert game.scores == {1: 0, 2: 0}
    victim.state = "canoe"

# A canoe running over a swimmer sinks it and scores; a swimmer cannot ram anyone.
game = arena(canoe_return=0)
rammer, swimmer = game.players[1], game.players[2]
game.hit(swimmer, by=1)
swimmer.invulnerability = 0
swimmer.pos.update(rammer.pos + (10, 0))
game.update(1 / 60, {})
assert swimmer.state == "eliminated" and game.scores[1] == 1 and "ram" in game.sounds
assert len(game.events) == 1 and game.events[0].player_id == 2, "Ramming feedback belongs only to the swimmer hit"

# The match ends at the end of a round once somebody leads with the goal; a shared lead plays on.
game = arena()
game.scores.update({1: 5, 2: 5})
game.players[2].state = "eliminated"
game.update(0, {})
assert game.phase == "round_over", "5-5 is overtime, not a win"
game = arena()
game.scores.update({1: 5, 2: 4})
game.players[2].state = "eliminated"
game.update(0, {})
assert game.phase == "match_over" and game.winner == 1

# The old rules are still there behind the settings.
game = arena(reload_mode="magazine", canoe_return=0, scoring="survivor", ram_swimmers=False)
game.players[2].pos.update(200, 650)
for shot in range(3):
    game.update(0, {})
    game.update(0, {1: PlayerInput(1, special=True)})
    if shot < 2: run(game, rules["shot_interval"] + .02)
assert game.players[1].reload > 2 and game.players[1].ammo == 0
run(game, 1.5, {1: PlayerInput(1, fire=True)})
assert len(game.rocks) <= 3, "the magazine rule locks the canoe out until the whole reload is done"

print("Rule checks passed: rock-by-rock reload, power-up during reload, canoe return with grace, kill scoring, "
      "no credit without a culprit, ramming, overtime, legacy rules")
