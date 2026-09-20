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
    argv: list = field(default_factory=lambda: ['beaver-battle', '--no-names', '--game', 'battle'])  # No keyboard for names, and these scenarios are Beaver Battle's.
    now: float = 100.0
    stage: int = 0
    frames: int = 0
    done: bool = False
    calibrated: bool = True
    fresh: bool = True
    active: list = field(default_factory=lambda: [1, 2])
    buttons: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    checkpoint: int = 0
    game: object = None
    vision: object = None
    posted: list = field(default_factory=list)
    missing_aim: bool = False
    press_edge: bool = False
    aim: tuple = (640, 360)
    survey_until: float = 0.0

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
        if mode == 'survey_match':
            pixels = pygame.surfarray.array3d(pygame.display.get_surface())
            assert (pixels == 255).all(), 'Scan projection must be blank, including cursor rings'
        if not self.history or self.history[-1][0] != mode:
            self.history.append((mode, state['elapsed']))

    def begin_calibration(self):
        self.calibrated = False

    def cancel_calibration(self):
        self.calibrated = True

    def begin_survey(self):
        self.survey_until = self.now + .5

    def run(self):
        self.game = Mock(phase='playing', scores={})
        self.game.update.return_value = []
        self.game.draw.side_effect = lambda surface: surface.fill((224, 239, 241))
        self.vision = Mock()
        self.vision.begin_survey.side_effect = self.begin_survey
        self.vision.surveying.side_effect = lambda: self.now < self.survey_until
        self.vision.snapshot.side_effect = lambda: VisionSnapshot(
            timestamp=self.now if self.fresh else self.now - 1, calibrated=self.calibrated)
        self.vision.begin_calibration.side_effect = self.begin_calibration
        self.vision.cancel_calibration.side_effect = self.cancel_calibration
        self.vision.draw_calibration.side_effect = lambda surface: surface.fill((255, 255, 255))
        bridge = Mock()
        bridge.active_ids.side_effect = lambda now: self.active[:]
        bridge.inputs.side_effect = lambda snapshot, now: {
            player: PlayerInput(player, None if self.missing_aim else self.aim, *self.buttons.get(player, (False, False)), special_pressed=self.press_edge and player == 1)
            for player in self.active}
        with contextlib.ExitStack() as patches:
            for target, name, value in (
                (pygame.time, 'Clock', lambda: SimpleNamespace(tick=self.tick)),
                (pygame.event, 'get', self.events), (pygame.display, 'flip', self.flip),
                (pygame.event, 'post', self.posted.append),
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
        if run.fault == 'disconnected' and state['mode'] == 'game':
            assert run.game.update.call_count == run.checkpoint
            assert not state['args'].simulate
            return []
        assert state['mode'] == 'pause'
        run.buttons[1] = (False, True)
        run.stage = 4
    elif run.stage == 4:
        assert state['mode'] == 'pause', f'{run.fault} Resume entered game'
        assert run.game.update.call_count == run.checkpoint, f'{run.fault} Resume advanced physics'
        run.done = True


def brief_reboot(run, state):
    if run.stage < 2:
        run.stage += 1
        return [key(pygame.K_RETURN)]
    if run.stage == 2 and state['mode'] == 'game':
        run.active = [] if run.fault == 'all' else [1]
        run.checkpoint = run.game.update.call_count
        run.reconnect_at = run.now + 1.5
        run.stage = 3
    elif run.stage == 3:
        assert state['mode'] == 'game' and not state['args'].simulate
        assert run.game.update.call_count == run.checkpoint
        if run.now >= run.reconnect_at:
            run.active = [1, 2]
            run.stage = 4
    elif run.stage == 4:
        assert state['mode'] == 'game'
        assert run.game.update.call_count > run.checkpoint
        run.done = True


def no_lasers(run, state):
    """With no controller connected and no camera, the mouse and keyboard run a whole match."""
    mode = state['mode']
    if run.stage == 0 and mode == 'lobby':
        run.stage = 1
        return [key(pygame.K_RETURN)]
    if run.stage == 1 and mode == 'game' and run.game.update.call_count >= 3:
        run.done = True


def new_match_same_game(run, state):
    """New match from the pause menu restarts the game that was paused, not whichever the chooser defaults to."""
    mode = state['mode']
    if run.stage == 0 and mode == 'dam' and run.frames >= 3:
        run.stage = 1
        return [key(pygame.K_ESCAPE)]
    if run.stage == 1 and mode == 'pause' and run.frames >= 8:  # The menu has been drawn a few times by now.
        run.stage = 2
        return [key(pygame.K_DOWN), key(pygame.K_RETURN)]
    if run.stage == 2 and run.frames >= 14:
        run.done = True


def one_controller(run, state):
    """One working controller and --bot: the computer takes the other number, readies up by itself, and the match
    runs with both canoes driven, so a demo survives a dead controller."""
    mode = state['mode']
    run.game.players = {}  # The stand-in game has no canoes; the bot then simply idles, connected.
    if mode == 'ready':
        run.buttons[2] = (False, True)  # The one person presses ready; nobody presses for the bot.
    if run.stage == 0 and mode == 'lobby' and run.frames >= 3:
        run.stage = 1
        return [key(pygame.K_RETURN)]
    if mode == 'game' and run.game.update.call_count >= 3:
        run.done = True


def laser_pointer(run, state):
    """With a controller connected, its laser dot hovers the menu in place of the trackpad."""
    if state['mode'] == 'lobby' and run.frames >= 3:
        run.done = True


def laser_select(run, state):
    if run.frames == 2:
        assert state['selection'] == 0, 'FIRE illuminates without cycling the menu'
    run.buttons[1] = (True, False)
    run.buttons[2] = (True, run.frames >= 2)
    if state['mode'] == 'home':
        run.done = True
        return []
    pending = run.posted[run.checkpoint:]
    run.checkpoint = len(run.posted)
    return pending


def laser_select_gap(run, state):
    run.press_edge = run.frames == 2
    run.missing_aim = run.frames <= (8 if run.fault == 'expired' else 3)
    if state['mode'] == 'home':
        assert run.fault != 'expired'
        run.done = True
    if run.frames == 10:
        assert run.fault == 'expired' and state['mode'] == 'lobby'
        assert not any(e.type == pygame.MOUSEBUTTONDOWN for e in run.posted)
        run.done = True
    pending = run.posted[run.checkpoint:]
    run.checkpoint = len(run.posted)
    return pending


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


def quit_to_menu(run, state):
    if run.stage == 0 and state['mode'] == 'lobby':
        run.stage = 1
        return [key(pygame.K_RETURN)]
    if run.stage == 1 and state['mode'] == 'ready':
        run.stage = 2
        return [key(pygame.K_RETURN)]
    if run.stage == 2 and state['mode'] == 'game':
        run.stage = 3
        run.checkpoint = run.game.update.call_count
        return [key(pygame.K_q)]
    if run.stage == 3:
        assert state['mode'] == 'home'
        assert run.game.update.call_count == run.checkpoint
        run.done = True


def main():
    pygame_mouse_at = (0, 0)  # Where the dummy video driver reports the mouse.
    Scenario(quit_to_menu).run()
    result = Scenario(lifecycle).run()
    assert [mode for mode, elapsed in result.history] == [
        'ready', 'survey_match', 'countdown', 'game', 'pause', 'lobby',
        'ready', 'survey_match', 'countdown', 'game', 'lobby']
    assert result.game.new_match.call_count == 3
    for index, (mode, elapsed) in enumerate(result.history):
        if mode == 'countdown':
            assert result.history[index + 1][1] - elapsed >= 3 - 1e-8
    for fault in ('one', 'all'):
        Scenario(brief_reboot, fault).run()
    for fault in ('disconnected', 'stale'):
        Scenario(unhealthy_resume, fault).run()
    for method in ('keyboard', 'controller'):
        Scenario(calibration_cancel, method).run()

    again = Scenario(new_match_same_game, active=[], calibrated=False, argv=['beaver-battle', '--no-names', '--game', 'dam']).run()
    assert [mode for mode, elapsed in again.history] == ['dam', 'pause', 'dam'], again.history

    alone = Scenario(one_controller, active=[2], argv=['beaver-battle', '--no-names', '--game', 'battle', '--bot']).run()
    visited = [mode for mode, elapsed in alone.history]
    assert visited[0] == 'lobby' and 'ready' in visited and visited[-2:] == ['countdown', 'game'], alone.history
    assert sorted(alone.game.new_match.call_args.args[0]) == [1, 2], 'the bot fills the missing seat'
    step, inputs, walls = alone.game.update.call_args.args
    assert set(inputs) >= {1, 2} and inputs[1].connected and inputs[2].connected, 'both canoes are driven'
    assert alone.game.names.get(1) == 'BOT', 'the bot took the number the person is not holding'

    fallback = Scenario(no_lasers, active=[], calibrated=False)
    fallback.run()
    assert [mode for mode, elapsed in fallback.history][-3:] == ['ready', 'countdown', 'game'], fallback.history
    step, inputs, walls = fallback.game.update.call_args.args
    assert set(inputs) == {1, 2} and inputs[1].connected and inputs[1].aim == pygame_mouse_at, 'player 1 follows the mouse'
    assert walls is not None and walls.dtype == bool and walls.any(), 'practice walls stand in for the camera'
    fallback.bridge.start.assert_called_once()
    fallback.bridge.feedback.assert_not_called()
    assert not fallback.posted, 'no laser, so nothing pretends to be one'

    pointed = Scenario(laser_pointer).run()
    hovers = [event.pos for event in pointed.posted if event.type == pygame.MOUSEMOTION]
    assert hovers and set(hovers) == {(640, 360)}, 'the lowest controller\'s dot hovers the menu'

    for fault in ('brief', 'expired'):
        Scenario(laser_select_gap, fault).run()
    border = Scenario(laser_select, aim=(712, 350)).run()
    assert any(mode == 'home' for mode, elapsed in border.history), 'Visible outline must be clickable'
    selected = Scenario(laser_select).run()
    clicks = [event for event in selected.posted if event.type == pygame.MOUSEBUTTONDOWN]
    assert len(clicks) == 1 and clicks[0].pos == (640, 360), 'Player 2 clicks its hovered item once per press'
    assert any(mode == 'home' for mode, elapsed in selected.history), 'Hover plus button 2 chooses Change game'
    with patch.object(pygame.draw, 'circle', wraps=pygame.draw.circle) as circles:
        Scenario(laser_pointer).run()
    rings = [call.args[1] for call in circles.call_args_list if len(call.args) >= 4 and call.args[3] == 19]
    assert (35, 90, 220) in rings and (20, 155, 115) in rings, 'Both player rings are drawn'

    bench = Scenario(bench_autostart, argv=['beaver-battle', '--calibrate', '--bench', '2'], active=[])
    bench.run()
    modes = [mode for mode, elapsed in bench.history]
    assert modes == ['calibration', 'survey_match', 'countdown', 'game'], modes
    bench.bridge.start.assert_not_called()
    bench.vision.start.assert_called_once()
    assert bench.game.new_match.call_count == 2, 'One startup match plus one built from the board scan'
    assert bench.game.update.call_count > 0, 'Bench must reach live physics without controllers'
    print('App lifecycle, guarded resume, new match keeps its game, one controller plus a bot, calibration cancellation, mouse fallback without lasers, laser as pointer, '
          'bench autostart, and cleanup checks passed.')


if __name__ == '__main__':
    main()
