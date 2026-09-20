"""Real geometry stays equivalent; slow work cannot block input/render updates."""
import os
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
import time
from threading import Event
from unittest.mock import patch
import cv2
import numpy as np
from beaver_battle.game import Game
from beaver_battle.geometry import GeometryWorker

config = dict(game=dict(width=320, height=180))
mask = np.zeros((180, 320), np.uint8)
cv2.rectangle(mask, (100, 40), (170, 130), 1, 3)
mask = mask.astype(bool)
mask.setflags(write=False)
expected = Game(config)
expected.new_match([1, 2], mask)
actual = Game(config)
actual.new_match([1, 2])
worker = GeometryWorker(config)
try:
    deadline = time.monotonic() + 5
    while not worker.update(actual, mask):
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert np.array_equal(actual.walls, expected.walls)
    assert np.array_equal(actual.wall_distance, expected.wall_distance)
finally:
    worker.stop()

entered, release = Event(), Event()
original = Game.build_walls
def stalled(self, walls):
    entered.set()
    assert release.wait(3)
    return original(self, walls)
with patch.object(Game, 'build_walls', stalled):
    worker = GeometryWorker(config)
    try:
        worker.update(actual, mask)
        assert entered.wait(2)
        start = time.monotonic()
        for frame in range(200):
            worker.update(actual, mask)
        assert time.monotonic() - start < .1, 'Rendering must not wait for geometry'
        worker.reset()
        release.set()
        time.sleep(.05)
        assert not worker.update(actual, None), 'Old calibration generation must not be applied'
    finally:
        release.set()
        worker.stop()
print('Geometry worker: real-mask equivalence, nonblocking updates, obsolete-generation rejection')

# Camera mutations and subsequent noisy frames cannot alter a running match.
initial = mask.copy()
static = Game(config)
static.new_match([1, 2], initial)
frozen = static.walls.copy()
initial[:] = True
static.update(0, {}, np.ones_like(mask))
assert np.array_equal(static.walls, frozen)
static.new_match([1, 2], np.zeros_like(mask))
assert not static.walls.any()
print('Match geometry stays fixed until a new match, including mutated input buffers')

# Preparing the initial snapshot must not advance players through unknown obstacles.
entered.clear()
release.clear()
with patch.object(Game, 'build_walls', stalled):
    waiting = Game(config)
    waiting.geometry = GeometryWorker(config)
    try:
        waiting.new_match([1, 2], mask)
        assert entered.wait(2)
        positions = {pid: player.pos.copy() for pid, player in waiting.players.items()}
        for frame in range(10):
            waiting.update(1 / 60, {}, np.ones_like(mask))
        assert waiting.time == 0
        assert all(waiting.players[pid].pos == pos for pid, pos in positions.items())
        release.set()
        deadline = time.monotonic() + 5
        while waiting.wall_source is not waiting.match_walls:
            waiting.update(0, {}, np.ones_like(mask))
            assert time.monotonic() < deadline
            time.sleep(.01)
        frozen = waiting.walls.copy()
        waiting.new_round()
        waiting.update(0, {}, np.zeros_like(mask))
        assert np.array_equal(waiting.walls, frozen), 'Rounds retain the match-start board'
    finally:
        release.set()
        waiting.geometry.stop()
print('Initial asynchronous geometry pauses physics, then stays fixed across rounds')
