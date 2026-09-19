"""Check output routing and hardware-free diagnostics with SDL's dummy backend."""

import contextlib
import io
import os
import sys
from unittest.mock import patch

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'

import pygame

from beaver_battle import app


def main():
    real_set_mode = pygame.display.set_mode
    selected = []

    def set_mode(size, flags=0, display=0):
        selected.append(display)
        assert flags & pygame.FULLSCREEN and flags & pygame.SCALED
        return real_set_mode(size, display=0)

    with patch.object(app, 'ControllerBridge', side_effect=AssertionError('diagnostic opened controllers')):
        with patch('beaver_battle.vision.Vision', side_effect=AssertionError('diagnostic opened camera')):
            with patch.object(sys, 'argv', ['beaver-battle', '--list-displays']):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    app.main()
                assert '0:' in output.getvalue()
                assert not pygame.get_init()
            with patch.object(sys, 'argv', ['beaver-battle', '--display', '99']):
                with contextlib.redirect_stderr(io.StringIO()):
                    try:
                        app.main()
                    except SystemExit as error:
                        assert error.code == 2
                    else:
                        raise AssertionError('missing display was silently accepted')
                assert not pygame.get_init()
            with patch.object(sys, 'argv', ['beaver-battle', '--display', '1', '--display-test', '--fullscreen', '--seconds', '.05']):
                with patch.object(pygame.display, 'get_desktop_sizes', return_value=[(2560, 1600), (1920, 1080)]):
                    with patch.object(pygame.display, 'set_mode', side_effect=set_mode):
                        app.main()
                assert selected == [1]
                assert not pygame.get_init()
    print('Display listing, missing-output rejection, routing, and hardware-free test passed.')


if __name__ == '__main__':
    main()
