import argparse
import json
import math
import os
import time
import tomllib
from pathlib import Path

import numpy as np

from beaver_battle.model import PlayerInput, VisionSnapshot
from beaver_battle.network import ControllerBridge, IdentityScheduler


def merge(base, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path):
    with path.open('rb') as source:
        config = tomllib.load(source)
    local = path.with_name('config.local.toml')
    if local.exists():
        with local.open('rb') as source:
            merge(config, tomllib.load(source))
    for group in ('display', 'game'):
        for key in ('width', 'height'):
            if not 320 <= config[group][key] <= 4096:
                raise ValueError(f'{group}.{key} must be between 320 and 4096')
    config['game']['width'] = config['display']['width']
    config['game']['height'] = config['display']['height']
    if not 2 <= config['game']['players'] <= 3:
        raise ValueError('game.players must be 2 or 3')
    if not 1 <= config['network']['port'] <= 65535:
        raise ValueError('network.port must be between 1 and 65535')
    return config


def simulated_inputs(config, elapsed, mouse=None, buttons=(False, False), player_ids=None):
    ids = player_ids or list(range(1, config['game']['players'] + 1))
    width, height = config['display']['width'], config['display']['height']
    result = {}
    for player_id in ids:
        angle = elapsed * 0.65 + player_id * math.tau / len(ids)
        aim = (width * (0.5 + 0.30 * math.cos(angle)), height * (0.5 + 0.30 * math.sin(angle * 1.3)))
        result[player_id] = PlayerInput(player_id, aim, True, int(elapsed * 2 + player_id) % 5 == 0)
    if mouse is not None and 1 in result:
        result[1] = PlayerInput(1, mouse, buttons[0], buttons[1])
    return result


def simulated_walls(width, height):
    import cv2
    walls = np.zeros((height, width), dtype=bool)
    walls[int(height * .20):int(height * .37), int(width * .28):int(width * .28) + 8] = True
    walls[int(height * .63):int(height * .80), int(width * .72):int(width * .72) + 8] = True
    walls[int(height * .42):int(height * .42) + 8, int(width * .57):int(width * .68)] = True
    walls[int(height * .58):int(height * .58) + 8, int(width * .24):int(width * .35)] = True
    # Closed outlines, as drawn with a marker: the game fills one as a log and one as a rock.
    ink = np.zeros((height, width), np.uint8)
    log = cv2.boxPoints(((width * .37, height * .84), (width * .17, height * .08), -12))
    cv2.polylines(ink, [log.astype(np.int32)], True, 1, 6)
    cv2.circle(ink, (int(width * .64), int(height * .19)), int(height * .07), 1, 6)
    return walls | ink.astype(bool)


def menu_choices(mode):
    if mode == 'lobby':
        return ['Start 2-player match', 'Start 3-player match', 'Calibrate board', 'Quit']
    return ['Resume', 'New match', 'Calibrate board', 'Quit']


def display_test(pygame, screen, display_index, seconds=0):
    width, height = screen.get_size()
    font = pygame.font.Font(None, max(24, width // 28))
    clock = pygame.time.Clock()
    started = time.monotonic()
    running = True
    while running:
        elapsed = time.monotonic() - started
        for event in pygame.event.get():
            if event.type == pygame.QUIT or event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
        for index, color in enumerate(((220, 30, 30), (30, 190, 30), (30, 50, 220))):
            pygame.draw.rect(screen, color, (index * width // 3, 0, width // 3 + 1, height))
        pygame.draw.rect(screen, 'white', screen.get_rect().inflate(-8, -8), 4)
        pygame.draw.circle(screen, 'yellow', (int(elapsed * 200) % width, height * 3 // 4), 20)
        for row, label in enumerate((f'BEAVER BATTLE — DISPLAY {display_index}',
                                     f'Logical canvas: {width} x {height}', 'All four white edges should be visible. Esc exits.')):
            rendered = font.render(label, True, 'white', 'black')
            screen.blit(rendered, rendered.get_rect(center=(width // 2, height // 3 + row * 50)))
        pygame.display.flip()
        clock.tick(30)
        if seconds and elapsed >= seconds:
            running = False


def main():
    parser = argparse.ArgumentParser(description='Beaver Battle: laser-tracked canoe combat')
    parser.add_argument('--config', type=Path, default=Path('config.toml'))
    parser.add_argument('--simulate', action='store_true', help='run a complete match with synthetic controllers and walls')
    parser.add_argument('--headless', action='store_true', help='run without a window, using simulated controllers')
    parser.add_argument('--seconds', type=float, default=0, help='exit after this many simulation seconds; 0 means unlimited')
    parser.add_argument('--screenshot', type=Path, help='save the last rendered frame')
    parser.add_argument('--report', type=Path, help='write a JSON smoke-test summary')
    parser.add_argument('--calibrate', action='store_true', help='start with projected calibration markers')
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--display', type=int, help='output display index from --list-displays')
    parser.add_argument('--list-displays', action='store_true', help='list connected outputs without starting camera or controllers')
    parser.add_argument('--list-cameras', action='store_true', help='probe camera indices without opening a window')
    parser.add_argument('--camera', type=int, help='camera device index from --list-cameras')
    parser.add_argument('--bench', type=int, choices=(2, 3), metavar='N',
                        help='real camera, calibration and board drawings with N bot canoes and no controllers')
    parser.add_argument('--display-test', action='store_true', help='show colors, edge border, and motion without camera or controllers; Esc exits')
    parser.add_argument('--players', type=int, choices=(2, 3))
    parser.add_argument('--mute', action='store_true', help='play no sound effects')
    parser.add_argument('--mouse', action='store_true', help='in simulation, control player 1 with mouse; left fires, right uses power-up')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.players:
        config['game']['players'] = args.players
    if args.camera is not None:
        config['camera']['device'] = args.camera
    if args.list_cameras:
        from beaver_battle.vision import probe_cameras
        for index, size in probe_cameras(config):
            print(f'{index}: {size[0]} x {size[1]}' if size else f'{index}: unavailable')
        return
    if args.headless:
        args.simulate = True
        os.environ['SDL_VIDEODRIVER'] = 'dummy'
        os.environ['SDL_AUDIODRIVER'] = 'dummy'
        if not args.seconds:
            args.seconds = 15
    if args.seconds < 0:
        parser.error('--seconds must be nonnegative')
    import pygame
    from beaver_battle.game import Game
    from beaver_battle.vision import Vision

    pygame.init()
    desktops = pygame.display.get_desktop_sizes()
    if args.list_displays:
        for index, size in enumerate(desktops):
            print(f'{index}: {size[0]} x {size[1]}')
        pygame.quit()
        return
    display_index = args.display if args.display is not None else config['display'].get('index', 0)
    if args.headless:
        display_index = 0
    if type(display_index) is not int or not 0 <= display_index < len(desktops):
        pygame.quit()
        parser.error(f'display {display_index!r} is unavailable; run --list-displays after connecting HDMI')
    width, height = config['display']['width'], config['display']['height']
    flags = pygame.FULLSCREEN if args.fullscreen or config['display']['fullscreen'] else 0
    if flags and not args.headless:
        flags |= pygame.SCALED
    screen = pygame.display.set_mode((width, height), flags, display=display_index)
    pygame.display.set_caption('Beaver Battle')
    if args.display_test:
        try:
            display_test(pygame, screen, display_index, args.seconds)
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(screen, str(args.screenshot))
        finally:
            pygame.quit()
        return
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 30)
    title_font = pygame.font.Font(None, 64)
    vision = Vision(config)
    bridge = ControllerBridge(config)
    scheduler = IdentityScheduler(config)
    game = Game(config)
    mode = 'game' if args.simulate else ('calibration' if args.calibrate else 'lobby')
    ids = list(range(1, config['game']['players'] + 1))
    walls = simulated_walls(width, height) if args.simulate else None
    snapshot = VisionSnapshot()
    elapsed = 0.0
    frame_count = 0
    accumulator = 0.0
    selection = 0
    ready = set()
    previous = {}
    both_since = None
    countdown_until = 0.0
    projection_due = 0.0
    menu_message = ''
    status = ''
    preview = False
    running = True
    feedback_count = 0
    game.new_match(ids, walls)

    from beaver_battle.sound import SoundBoard
    sound = config.get('sound', {})
    board = SoundBoard(sound.get('enabled', True) and not args.mute and not args.headless, sound.get('volume', .6))

    def text_line(text, y, large=False, color=None):
        from beaver_battle import sprites
        selected = text.startswith('> ')
        fill = color or (sprites.PINK_DARK if large else sprites.BUTTER if selected else sprites.WHITE)
        surface = sprites.label(text[2:] if selected else text, 76 if large else 38 if selected else 32, fill, tilt=2 if large else 0)
        screen.blit(surface, ((width - surface.get_width()) // 2, y - (14 if large else 0)))

    try:
        if not args.simulate:
            if not args.bench:
                bridge.start()
            vision.start()
            if mode == 'calibration':
                vision.begin_calibration()
        while running:
            dt = 1 / 60 if args.headless else min(clock.tick(config['display']['fps']) / 1000, .1)
            now = time.monotonic()
            elapsed += dt
            frame_count += 1
            key_confirm = False
            key_cycle = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if mode == 'game':
                            mode = 'pause'
                            selection = 0
                        elif mode == 'pause':
                            mode = 'game'
                        else:
                            if mode == 'calibration':
                                vision.cancel_calibration()
                            mode = 'lobby'
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        key_confirm = True
                    elif event.key in (pygame.K_DOWN, pygame.K_TAB):
                        key_cycle = True
                    elif event.key == pygame.K_F2:
                        preview = not preview
                    elif event.key == pygame.K_c and not args.simulate:
                        mode = 'calibration'
                        vision.begin_calibration()
                    elif event.key == pygame.K_r and args.simulate:
                        game.new_match(ids, walls)
                        mode = 'game'
            if args.simulate:
                mouse = pygame.mouse.get_pos() if args.mouse else None
                pressed = pygame.mouse.get_pressed(3)
                inputs = simulated_inputs(config, elapsed, mouse, (pressed[0], pressed[2]), ids)
                active_ids = ids
            elif args.bench:
                snapshot = vision.snapshot()
                walls = snapshot.walls
                active_ids = list(range(1, args.bench + 1))
                inputs = simulated_inputs(config, elapsed, None, (False, False), active_ids)
            else:
                bridge.poll(now)
                snapshot = vision.snapshot()
                walls = snapshot.walls
                inputs = bridge.inputs(snapshot, now)
                active_ids = bridge.active_ids(now)
                scheduler.update(now, bridge, vision, mode not in ('calibration', 'pause'))
                bridge.send(now)
            host = min(active_ids, default=1)
            host_input = inputs.get(host, PlayerInput(host, connected=False))
            old_fire, old_special = previous.get(host, (False, False))
            driven = args.simulate or args.bench
            cycle = key_cycle or (not driven and host_input.fire and not old_fire)
            confirm = key_confirm or (not driven and host_input.special and not old_special)
            if mode in ('game', 'ready', 'countdown', 'calibration') and host_input.fire and host_input.special and not driven:
                both_since = now if both_since is None else both_since
                if now - both_since >= 1:
                    if mode == 'calibration':
                        vision.cancel_calibration()
                    mode, selection = ('pause' if mode == 'game' else 'lobby'), 0
                    cycle, confirm = False, False
                    both_since = None
            else:
                both_since = None
            if mode in ('lobby', 'pause'):
                choices = menu_choices(mode)
                if cycle:
                    selection = (selection + 1) % len(choices)
                if confirm:
                    choice = choices[selection]
                    if choice == 'Quit':
                        running = False
                    elif choice == 'Calibrate board':
                        if args.simulate:
                            menu_message = 'Calibration uses the real USB camera.'
                        else:
                            mode = 'calibration'
                            vision.begin_calibration()
                    elif choice == 'Resume':
                        mode, status = 'game', ''
                    elif choice == 'New match':
                        mode, selection = 'lobby', 0
                    else:
                        count = 2 if '2-player' in choice else 3
                        if len(active_ids) < count:
                            menu_message = f'Waiting for {count} controllers; {len(active_ids)} connected.'
                        elif not args.simulate and not snapshot.calibrated:
                            menu_message = 'Calibrate the board before starting.'
                        else:
                            ids, ready, mode = active_ids[:count], set(), 'ready'
                            menu_message = ''
            elif mode == 'ready':
                for player_id in ids:
                    current = inputs.get(player_id, PlayerInput(player_id, connected=False))
                    if current.special and not previous.get(player_id, (False, False))[1]:
                        ready.add(player_id)
                if key_confirm or args.simulate:
                    ready.update(ids)
                if all(player_id in ready and player_id in active_ids for player_id in ids):
                    game.new_match(ids, walls)
                    mode, countdown_until = 'countdown', elapsed + 3.0
            elif mode == 'countdown':
                if not args.bench and any(player_id not in active_ids for player_id in ids):
                    mode, ready = 'ready', set()
                elif elapsed >= countdown_until:
                    mode, accumulator = 'game', 0.0
            elif mode == 'calibration' and snapshot.calibrated:
                mode, selection, menu_message = 'lobby', 0, 'Calibration saved.'
            if args.bench and mode == 'lobby' and snapshot.calibrated:
                ids = active_ids
                game.new_match(ids, walls)
                mode, countdown_until, menu_message = 'countdown', elapsed + 3.0, ''
            if mode == 'game' and not args.simulate:
                if not args.bench and any(player_id not in active_ids for player_id in ids):
                    mode, selection, status = 'pause', 0, 'Controller disconnected. Reconnect, then resume.'
                elif now - snapshot.timestamp > config['camera']['stale_seconds'] or not snapshot.calibrated:
                    mode, selection, status = 'pause', 0, 'Camera tracking unavailable. Restore camera or calibrate.'
            if mode == 'game':
                accumulator += dt
                while accumulator >= 1 / 60:
                    if game.held(1 / 60) is True:
                        accumulator -= 1 / 60
                        continue
                    events = game.update(1 / 60, inputs, walls)
                    if isinstance(game.sounds, list):
                        board.play(game.sounds)
                        game.sounds.clear()
                    feedback_count += len(events)
                    if not args.simulate:
                        bridge.feedback(events, now)
                    accumulator -= 1 / 60
                if game.phase == 'match_over' and confirm:
                    mode, selection = 'lobby', 0
            else:
                accumulator = 0
            if mode in ('survey', 'survey_match'):
                # Show the map settling, so everyone sees the board they will play on.
                screen.fill((224, 239, 241))
                if walls is not None and walls.any():
                    board = pygame.Surface((width, height), pygame.SRCALPHA)
                    pixels, opacity = pygame.surfarray.pixels3d(board), pygame.surfarray.pixels_alpha(board)
                    pixels[walls.T] = (92, 74, 62)
                    opacity[walls.T] = 255
                    del pixels, opacity
                    screen.blit(board, (0, 0))
                text_line('READING THE BOARD', 70, True)
                share = vision.survey_progress()
                bar = pygame.Rect(width // 4, height - 130, width // 2, 18)
                pygame.draw.rect(screen, (150, 165, 190), bar, 2, border_radius=9)
                filled = bar.inflate(-6, -6)
                filled.width = max(1, int(filled.width * share))
                pygame.draw.rect(screen, (92, 74, 62), filled, border_radius=6)
                text_line('Keep hands off the board', height - 95)
            elif mode == 'calibration':
                vision.draw_calibration(screen)
                # Name the corner that is blocked, on the board, where the operator is standing.
                text_line(snapshot.error or 'Keep all four markers in view', height // 2 + 60)
                text_line('Keep ink and hands out of the four corners. Hold both buttons to cancel.', height // 2 + 96)
            elif mode in ('game', 'countdown'):
                game.draw(screen)
                if mode == 'countdown':
                    text_line(str(max(1, math.ceil(countdown_until - elapsed))), height // 2 - 32, True)
                    text_line('Hold both buttons to cancel', height - 45)
            else:
                screen.fill((224, 239, 241))
                text_line('BEAVER BATTLE', 80, True)
                if mode == 'ready':
                    text_line('Press button 2 to ready', 180)
                    for index, player_id in enumerate(ids):
                        text_line(f'Player {player_id}: {"READY" if player_id in ready else "waiting"}', 260 + index * 55)
                else:
                    for index, choice in enumerate(menu_choices(mode)):
                        text_line(('> ' if index == selection else '') + choice, 240 + index * 55)
                text_line(menu_message or status or ('Hold both buttons to cancel' if mode == 'ready' else 'Button 1: choose     Button 2: confirm'), height - 100)
                camera_status = 'simulated' if args.simulate else (snapshot.error or ('calibrated' if snapshot.calibrated else 'needs calibration'))
                text_line(f'Connected: {active_ids}    Camera: {camera_status}', height - 55)
            if preview and mode in ('lobby', 'pause') and snapshot.preview is not None:
                import cv2
                image = cv2.cvtColor(snapshot.preview, cv2.COLOR_BGR2RGB)
                image = pygame.surfarray.make_surface(np.transpose(image, (1, 0, 2)))
                screen.blit(pygame.transform.smoothscale(image, (width // 4, height // 4)), (width * 3 // 4, 0))
            pygame.display.flip()
            # Vision needs what we just projected to tell a shadow from dark artwork.
            # Reading the framebuffer costs real time, so only at the wall update rate.
            if not args.simulate and mode in ('game', 'countdown') and now >= projection_due:
                projection_due = now + 1 / max(1.0, float(config['camera'].get('wall_update_hz', 10)))
                vision.set_projection(np.transpose(pygame.surfarray.array3d(screen), (1, 0, 2)), now)
            previous = {player_id: (value.fire, value.special) for player_id, value in inputs.items()}
            if args.seconds and elapsed >= args.seconds:
                running = False
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            pygame.image.save(screen, str(args.screenshot))
        report = dict(frames=frame_count, simulation_seconds=round(elapsed, 3), mode=mode, phase=game.phase,
                      scores=game.scores, feedback_events=feedback_count, simulated=args.simulate,
                      projector=config['projector'], display_index=display_index,
                      camera_calibrated=bool(snapshot.calibrated),
                      camera_frame_available=snapshot.preview is not None)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    finally:
        bridge.stop()
        if not args.simulate:
            vision.stop()
        pygame.quit()
