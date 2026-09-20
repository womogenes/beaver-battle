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
game.new_match([1, 2], walls)
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
- First damage ejects a beaver; next damage, or a canoe running it over, eliminates it, and a beaver that survives `canoe_return` seconds gets a new canoe with `return_invulnerability` seconds of grace. The sinker scores (`scoring = "kills"`). Every successful hit grants 0.8 s invulnerability and produces one feedback event. Beavers move at 75 px/s and hold button 1 to accelerate toward 150 px/s. They cannot collect or activate weapons.
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

### Closed outlines become logs and rocks

Every new mask is first repaired, then searched for enclosures.

`link_strokes` rejoins a stroke the camera broke into pieces. A line drawn in one movement arrives in fragments wherever the pen ran dry or the ink went faint; on the bench one curve came through in seven pieces with 36 to 65 pixel holes between them, and a canoe is 36 pixels across, so it sailed straight through. A player who drew one continuous line is entitled to one continuous wall, so fragments whose nearest points come within `game.stroke_link` are joined by the shortest segment between them. A stroke that is already whole gains nothing. Candidate pairs come from one distance transform: where two background pixels side by side are nearest to different pieces, the sum of their distances is the width of the channel between those pieces, and only the pairs that pass pay for an exact search. Drawings further apart than the reach are never joined, and fragments below `game.stroke_min_piece` are specks rather than strokes.

`mend_breaks` then carries any free end of the drawing on to whatever it was heading for, within `game.stroke_mend` and `game.stroke_spread` degrees of its own direction. This reaches the case linking cannot: a stroke interrupted part way round a loop, whose two sides are still one piece because they meet somewhere else entirely. No test based on connectivity can see that gap, and a canoe fits through it. Free ends come from a thinned copy of the ink, keeping only ends whose branch is at least `stroke_min_branch` long, since thinning a hand-drawn blob sprouts spurs all over it and every spur looks like an end. Requiring a free end, and requiring the ink to lie ahead of the stroke rather than beside it, is what stops a spiral or a letter C being welded shut: their arms come close, but neither ends pointing at the other.

Linking is therefore kept short and mending long. Blind linking joins whatever is near, so at 70 pixels it welded adjacent handwriting into an enclosure; mending is directional, so it can reach much further in safety.

This repairs holes in a barrier. It does not close a barrier that was never closed: an open curve still leaves open water past each of its ends, and a canoe is entitled to sail round it. To divide the board, draw to the board's edge.

Every open stroke is then drawn, at `game.ink_width`, whether or not it is straight. A long straight one is rendered as a stick and anything curved as a bark-coloured line following its own shape, but both are equally solid and both are equally visible. A stroke that blocks a canoe while staying invisible reads as the game ignoring the drawing, which is what a faint pen under a bright projector looks like from across the room.

Then regions enclosed by ink are found (`closed_shapes` in `game.py`). Each enclosure is added to the solid mask, so outline plus interior collide exactly like ink, and the interior is projected with a bright texture: a long enclosure (minimum-area rectangle at least 2:1) is a log with grain along its long axis, anything rounder is a rock. `Game.walls` is ink plus interiors; `Game.shapes` lists the current enclosures.

- A hand-drawn outline almost never closes, and the camera breaks it further wherever the pen ran dry, so requiring a watertight loop filled very little of a real board. Ink is grown outward at increasing radii and an enclosure is taken at the first radius that reveals it, provided the bridged gap is at most `game.shape_closure` of the enclosure's linear extent (default 0.20).
- That ratio is the whole judgement, and it is what separates a circle with a pen lift from a letter C. Both are rings with a gap; only one has a gap small beside what it surrounds. An absolute pixel tolerance cannot make that call, since a large shape can be missing far more ink than a small one and still plainly be a container.
- Ink is grown, not closed. A morphological closing joins two stroke ends when it dilates and severs them again when it erodes, so it needs a radius several times the gap being bridged and the ratio stops meaning anything. One distance transform of the background serves every radius, which is what keeps the search near 6 ms at 1280x720 instead of 100.
- The search reruns on every published mask (up to `camera.wall_update_hz`). Erasing reopens the water within one wall-persistence interval, but the erasure now has to be proportionally significant: a gap above `shape_closure` of the shape's extent, roughly a marker-width swipe across a hand-sized circle. Bodies caught inside a newly closed outline are relocated like any other new ink.
- The enclosure is measured at its restored size, not as the hole was found. Growing the ink inward eats most of a small ring's interior, so a bitten ring measured where it was found looks far too small to be an arena and was refused for it. The outline handed to the art is restored the same way, or a filled body would be drawn smaller than it collides.
- `shape_closure` is deliberately generous at 0.70, so a shape missing most of a radius still fills. A long curve that almost closes becomes one rock rather than a row of sticks, which is steadier to look at as well as truer to what was drawn. The guard is `ring_half_open`: half a ring encloses nothing at any tolerance, and `letter_c` starts filling at 0.75, so the working margin above 0.70 is narrow. Lower it if open drawings start flooding.
- Enclosures below `game.shape_min_area` (default 1200 px², about a canoe, which keeps letter counters out) or above `game.shape_max_fraction` of the board (default 0.25, an arena border) stay hollow. A region bounded partly by the board edge is open water, not an enclosure.
- `checks/board_shapes.py` holds the shapes a whiteboard actually carries, and `checks.check_shapes` runs them: clean and gapped and dashed rings, boxes, triangles, a flowchart, Venn lobes, nested outlines, a plot whose curve meets its axes, against a letter C, an open swoosh, a spiral, two lines of handwriting, an arena border and a sub-canoe box that must all stay hollow. Filling is what turns someone's homework into a map, so the cases are checked individually rather than by eye.
- Fills are projected only on the bright interior, never relied on to cover ink, and both palettes stay far above `camera.wall_threshold` and below the laser redness test, so the projection cannot erase its own outline or fake a laser. The renderer check covers a drawn log and rock.
- A fill keeps its previous kind while its aspect ratio is between 1.7 and 2.3, and its previous grain angle while the new one is within 8°, matched by center within 24 px. Rock speckle is anchored to the board. Camera jitter therefore does not make textures shimmer.

Only synthetic masks have been checked. Whether a real camera sees the projected fill as reliably bright next to real ink still needs the mounted setup.

Collision uses a shared distance map for physical ink and direct circle/rectangle tests for the small virtual arena. Motion is substepped and projectiles use swept collision. This keeps the engine independent of the vision implementation without a general physics framework.

Placeholder polygons, text, effects, and backgrounds use bright colors so they do not look like dark physical ink or isolated red laser dots. Optional `assets/canoe.png` and `assets/beaver.png` replace player polygons; sprites point right before rotation and should use transparency plus the same bright palette. `game.sprite_dir` can select another asset directory. The engine deliberately does not create or load any other game assets.
