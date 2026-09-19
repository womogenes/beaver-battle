# Beaver Battle game engine

Run the game checks without controllers, camera, projector, or a desktop:

```bash
.venv/bin/python -m checks.check_game
.venv/bin/python -m checks.check_game /tmp/beaver-battle.png
```

The optional image is a complete initial arena render. Checks exercise every weapon and hazard, ammo timing, two-stage damage, unique feedback IDs, first-to-five scoring, ties, fast projectile collision, live wall insertion/removal, floating/thrust, and bright rendering. They do not establish physical tracking, projection latency, or water-actuator performance.

## Integration

```python
game = Game(config)
game.new_match([1, 2, 3], walls)
events = game.update(1 / 60, inputs, walls)
game.draw(surface)
```

`inputs` maps controller IDs to `PlayerInput`. `walls` is an immutable boolean NumPy array shaped `(game height, game width)`; true pixels are solid. Publish a new array when the detected map changes. Passing `None` to `update` retains the current map. A new match with `walls=None` clears it.

`phase` is `playing`, `round_over`, or `match_over`; `scores` maps player IDs to points. `winner` identifies the round/match winner or is `None` for a tie. `blocked` and `error` report an arena with insufficient free space. Simulation pauses while blocked and resumes after a usable new wall snapshot. The launcher owns lobby, calibration, manual pause, disconnect handling, and returning to the lobby after a match. One-player mode is a practice arena and never awards automatic survival points.

Each hit emits a `FeedbackEvent`; its ID increases across rounds and matches on the same `Game` instance. The controller bridge decides whether to send it and implements delivery/duplicate protection. Drawing has no networking or camera dependencies.

## Rules and controls

All distances below are for 1280×720 and scale with arena size. Game settings in `config.toml` control motion, fire rate, magazine size, recharge duration, invulnerability, score target, and round delay.

- Canoes always move at 240 px/s, steering toward a valid laser at up to 240°/s. Aim within 24 px, stale aim, ambiguity, or missing aim preserves heading. Wall contact reflects motion without damage. Players can overlap each other.
- Button 1 holds continuous fire: one rock every 0.30 s, three shots, then a 2.5 s full-magazine reload. Partially used magazines do not recharge. During reload, firing and new power-up activation are blocked; existing attacks remain active.
- Rocks travel at 650 px/s, expire after two seconds, and stop at their first impact. Their owner cannot be hit. Turret rocks are neutral and can hit any player. Swept collision also checks the muzzle path, preventing shots through a nearby thin wall.
- First damage ejects a beaver; next damage eliminates it. Every successful hit grants 0.8 s invulnerability and produces one feedback event. Beavers move at 75 px/s and hold button 1 to accelerate toward 150 px/s. They cannot collect or activate weapons.
- Last survivor scores one point. A simultaneous elimination is a draw. Rounds restart after three seconds, rebuilding projected objects while preserving physical walls. First to five wins.
- Button 2 activates one stored pickup on its press edge. Holding it never activates a newly collected replacement. A pickup remains on the board if the canoe is already carrying one.

## Complete arena content

The initial map contains two destructible wall segments, one barrier, two moving asteroids, three barrels, two turrets, and one death-beam emitter. These are projected objects, separate from indestructible physical ink.

| Object | Behavior |
| --- | --- |
| Walls and barrier | Three HP; destroyed geometry immediately opens a route. |
| Asteroids | Two HP; drift at 42 px/s and bounce against solid walls and props. Contact causes no player damage. |
| Barrels | One HP; stationary. |
| Turrets | Three HP; aim toward the closest visible player and fire every 1.2 s. Walls and props block targeting and projectiles. |
| Death-beam emitter | Three HP; downward beam with a one-second warning, half-second active period, and 3.5 seconds idle. Solid obstacles block the beam. Each player can be hit only once per activation. |

Destroyed barrels and asteroids always drop a pickup. A shuffled bag of the three weapon types provides random ordering and ensures variety. Pickups drift at 35 px/s, bounce off solids, and expire after 20 seconds.

| Weapon | Button 2 behavior |
| --- | --- |
| Laser (`L`) | An instant forward ray stops at the first obstacle, player, or mine. One player damage or three prop damage; the bright beam remains visible for 0.15 s. |
| Jouster (`J`) | A two-second lance and 1.5× speed boost. The first offensive contact deals one player damage or three prop damage and ends the lance. Body impacts remain harmless. |
| Mine (`M`) | Places a mine behind the canoe in a free location. Arms after 0.6 s; triggers within 60 px of an opponent. Its 90 px blast respects physical/virtual cover and never damages its owner. Shooting it detonates it. Untriggered mines expire after 15 s. |

## Live drawings and rendering

New physical ink displaces overlapping players, pickups, and projected props to the nearest valid position without damage. Projectiles/mines covered by new ink disappear without exploding. If an object cannot fit anywhere, the game shows “Clear space on the board” and waits for a new mask. Erasing ink restores traversable space. Camera updates never restore a destroyed projected prop; only the next round does.

Collision uses a shared distance map for physical ink and direct circle/rectangle tests for the small virtual arena. Motion is substepped and projectiles use swept collision. This keeps the engine independent of the vision implementation without a general physics framework.

Placeholder polygons, text, effects, and backgrounds use bright colors so they do not look like dark physical ink or isolated red laser dots. Optional `assets/canoe.png` and `assets/beaver.png` replace player polygons; sprites point right before rotation and should use transparency plus the same bright palette. `game.sprite_dir` can select another asset directory. The engine deliberately does not create or load any other game assets.
