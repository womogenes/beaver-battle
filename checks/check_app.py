"""Exercise the real app loop without camera, network, or a visible window."""

import contextlib
import io
import os
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'

import pygame

from beaver_battle import app, game, vision
from beaver_battle.model import PlayerInput, VisionSnapshot


def key(value):
    return pygame.event.Event(pygame.KEYDOWN, key=value)


@dataclass
class Scenario:
    script: object
    fault: str = ''
    argv: list = field(default_factory=lambda: ['beaver-battle'])
    now: float = 100.0
    stage: int = 0
    frames: int = 0
    done: bool = False
    calibrated: bool = True
    fresh: bool = True
    active: list = field(default_factory=lambda: [1, 2, 3])
    buttons: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    checkpoint: int = 0
    game: object = None
    vision: object = None

    def tick(self, fps):
        self.now += .1
        return 100

    def events(self):
        self.frames += 1
        assert self.frames < 120, f'Lifecycle stalled: {self.history}'
        self.buttons.clear()
        events = self.script(self, sys._getframe(1).f_locals) or []
        if self.done:
            events.append(pygame.event.Event(pygame.QUIT))
        return events

    def flip(self):
        state = sys._getframe(1).f_locals
        mode = state['mode']
        if not self.history or self.history[-1][0] != mode:
            self.history.append((mode, state['elapsed']))

    def begin_calibration(self):
        self.calibrated = False

    def cancel_calibration(self):
        self.calibrated = True

    def run(self):
        self.game = Mock(phase='playing', scores={})
        self.game.update.return_value = []
        self.game.draw.side_effect = lambda surface: surface.fill((224, 239, 241))
        self.vision = Mock()
        self.vision.snapshot.side_effect = lambda: VisionSnapshot(
            timestamp=self.now if self.fresh else self.now - 1, calibrated=self.calibrated)
        self.vision.begin_calibration.side_effect = self.begin_calibration
        self.vision.cancel_calibration.side_effect = self.cancel_calibration
        self.vision.draw_calibration.side_effect = lambda surface: surface.fill((255, 255, 255))
        bridge = Mock()
        bridge.active_ids.side_effect = lambda now: self.active[:]
        bridge.inputs.side_effect = lambda snapshot, now: {
            player: PlayerInput(player, (640, 360), *self.buttons.get(player, (False, False)))
            for player in self.active}
        with contextlib.ExitStack() as patches:
            for target, name, value in (
                (pygame.time, 'Clock', lambda: SimpleNamespace(tick=self.tick)),
                (pygame.event, 'get', self.events), (pygame.display, 'flip', self.flip),
                (app.time, 'monotonic', lambda: self.now),
                (app, 'ControllerBridge', lambda config: bridge),
                (app, 'IdentityScheduler', lambda config: Mock()),
                (vision, 'Vision', lambda config: self.vision),
                (game, 'Game', lambda config: self.game),
                (sys, 'argv', self.argv),
            ):
                patches.enter_context(patch.object(target, name, value))
            with contextlib.redirect_stdout(io.StringIO()):
                app.main()
        assert self.done
        self.bridge = bridge
        bridge.stop.assert_called_once()
        self.vision.stop.assert_called_once()
        assert not pygame.get_init(), 'pygame must close on exit'
        return self


def lifecycle(run, state):
    mode = state['mode']
    actions = [
        ('lobby', pygame.K_RETURN), ('ready', pygame.K_RETURN),
        ('game', pygame.K_ESCAPE), ('pause', pygame.K_DOWN),
        ('pause', pygame.K_RETURN), ('lobby', pygame.K_RETURN),
        ('ready', pygame.K_RETURN),
    ]
    if run.stage < len(actions):
        expected, button = actions[run.stage]
        if mode == expected:
            run.stage += 1
            return [key(button)]
    elif run.stage == 7 and mode == 'game':
        run.game.phase = 'match_over'
        run.stage = 8
    elif run.stage == 8:
        run.buttons[1] = (False, True)
        run.stage = 9
    elif run.stage == 9:
        assert mode == 'lobby'
        run.done = True


def unhealthy_resume(run, state):
    if run.stage < 2:
        assert state['mode'] == ('lobby' if run.stage == 0 else 'ready')
        run.stage += 1
        return [key(pygame.K_RETURN)]
    if run.stage == 2 and state['mode'] == 'game':
        if run.fault == 'disconnected':
            run.active = [1]
        else:
            run.fresh = False
        run.checkpoint = run.game.update.call_count
        run.stage = 3
    elif run.stage == 3:
        assert state['mode'] == 'pause'
        run.buttons[1] = (False, True)
        run.stage = 4
    elif run.stage == 4:
        assert state['mode'] == 'pause', f'{run.fault} Resume entered game'
        assert run.game.update.call_count == run.checkpoint, f'{run.fault} Resume advanced physics'
        run.done = True


def calibration_cancel(run, state):
    if run.stage == 0:
        run.stage = 1
        return [key(pygame.K_c)]
    if state['mode'] == 'calibration':
        if run.fault == 'keyboard':
            return [key(pygame.K_ESCAPE)]
        run.buttons[1] = (True, True)
    else:
        assert state['mode'] == 'lobby'
        run.vision.cancel_calibration.assert_called_once()
        assert run.calibrated
        run.done = True


def bench_autostart(run, state):
    """The bench runs the real camera path with no controllers connected at all."""
    if run.stage == 0:
        assert state['mode'] == 'calibration'
        run.stage = 1
    elif run.stage == 1:
        assert state['mode'] == 'calibration', 'Markers must stay up until calibration succeeds'
        run.calibrated = True
        run.stage = 2
    elif run.stage == 2 and state['mode'] == 'game':
        run.stage = 3
        run.done = True


def main():
    result = Scenario(lifecycle).run()
    assert [mode for mode, elapsed in result.history] == [
        'ready', 'countdown', 'game', 'pause', 'lobby',
        'ready', 'countdown', 'game', 'lobby']
    assert result.game.new_match.call_count == 3
    for index, (mode, elapsed) in enumerate(result.history):
        if mode == 'countdown':
            assert result.history[index + 1][1] - elapsed >= 3 - 1e-8
    for fault in ('disconnected', 'stale'):
        Scenario(unhealthy_resume, fault).run()
    for method in ('keyboard', 'controller'):
        Scenario(calibration_cancel, method).run()

    bench = Scenario(bench_autostart, argv=['beaver-battle', '--calibrate', '--bench', '3'], active=[])
    bench.run()
    modes = [mode for mode, elapsed in bench.history]
    assert modes == ['calibration', 'countdown', 'game'], modes
    bench.bridge.start.assert_not_called()
    bench.vision.start.assert_called_once()
    assert bench.game.new_match.call_count == 2, 'One startup match plus one built from the board scan'
    assert bench.game.update.call_count > 0, 'Bench must reach live physics without controllers'
    print('App lifecycle, guarded resume, calibration cancellation, bench autostart, and cleanup checks passed.')


if __name__ == '__main__':
    main()
