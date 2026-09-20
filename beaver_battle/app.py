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
    if config['game']['players'] != 2:
        raise ValueError('game.players must be 2')
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
        return ['Start match', 'Calibrate board', 'How to play', 'Change game', 'Quit']
    return ['Resume', 'New match', 'Calibrate board', 'How to play', 'Change game', 'Quit']


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
    parser.add_argument('--diagnostics-dir', type=Path, help='save live camera, wall mask and status once per second')
    parser.add_argument('--report', type=Path, help='write a JSON smoke-test summary')
    parser.add_argument('--calibrate', action='store_true', help='start with projected calibration markers')
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--display', type=int, help='output display index from --list-displays')
    parser.add_argument('--list-displays', action='store_true', help='list connected outputs without starting camera or controllers')
    parser.add_argument('--list-cameras', action='store_true', help='probe camera indices without opening a window')
    parser.add_argument('--camera', type=int, help='camera device index from --list-cameras')
    parser.add_argument('--bench', type=int, choices=(2,), metavar='N',
                        help='real camera, calibration and board drawings with N bot canoes and no controllers')
    parser.add_argument('--display-test', action='store_true', help='show colors, edge border, and motion without camera or controllers; Esc exits')
    parser.add_argument('--players', type=int, choices=(2,))
    parser.add_argument('--game', choices=('battle', 'treasure', 'solo', 'dam'), help='go straight to one game instead of the chooser')
    parser.add_argument('--no-names', action='store_true', help='skip typing player names before a match')
    parser.add_argument('--mute', action='store_true', help='play no sound effects')
    parser.add_argument('--mouse', action='store_true', help='in simulation, control player 1 with mouse; left boosts swimmers, right fires or uses power-up')
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
    from beaver_battle import sprites
    from beaver_battle.game import Game
    from beaver_battle.dam import DamIt
    from beaver_battle.leaderboard import Leaderboard
    from beaver_battle.treasure import SOLO_BOARDS, Treasure
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
    if not args.simulate:
        from beaver_battle.geometry import GeometryWorker
        game.geometry = GeometryWorker(config)
    mode = 'game' if args.simulate else ('calibration' if args.calibrate else 'lobby')
    # Names are typed on the laptop before a match; unattended runs skip the question.
    ask_names = not (args.headless or args.bench or args.seconds or args.no_names)
    names, naming, typed = {}, 0, ''
    info_return, info_frame = 'lobby', 0
    pause_button = pygame.Rect(width - 58, 12, 40, 40)
    info_button = pygame.Rect(width - 108, 12, 40, 40)
    again_button = pygame.Rect(0, 0, 300, 66)
    again_button.center = (width // 2 - 170, height - 96)
    menu_button = pygame.Rect(0, 0, 220, 66)
    menu_button.center = (width // 2 + 210, height - 96)
    again = False
    menu_rects = []
    if mode == 'game' and ask_names:
        mode = 'names'
    # Two games share this launcher. The chooser comes first unless a flag or an unattended run picks one.
    treasure = Treasure(config)
    chosen = args.game or 'battle'
    games = ('battle', 'treasure', 'dam')  # One or two players is a question inside Treasure Dash, not another game.
    dam = DamIt(config)
    count_pick, count_rects = 1, []
    map_pick, map_rects, map_views = 0, [], {}
    scores = Leaderboard(config.get('treasure', {}).get('leaderboard_file', 'leaderboard.json'))
    solo_board, recorded = 0, False
    next_button = pygame.Rect(0, 0, 300, 66)
    scores_button = pygame.Rect(0, 0, 360, 50)
    scores_button.center = (width // 2, height - 40)
    resume_mode, home_pick, home_rects = 'game', 0, []
    unattended = args.headless or args.bench or args.seconds or args.calibrate
    if args.game is None and not unattended:
        mode = 'home'
    elif chosen == 'dam' and not unattended:
        ids = [1, 2]
        mode = 'names' if ask_names else 'dam'
        dam.new_match(ids)
    elif chosen in ('treasure', 'solo') and not unattended:
        ids = [1] if chosen == 'solo' else [1, 2]
        mode = 'names' if ask_names else 'treasure'
        treasure.new_match(ids, (2,) if args.simulate and chosen == 'treasure' else (), solo_board if chosen == 'solo' else None, chosen == 'solo')
        treasure.standings = scores.top(treasure.board['name']) if chosen == 'solo' else []

    def active_game():
        playing_now = resume_mode if mode in ('pause', 'info') else mode
        return treasure if playing_now == 'treasure' else dam if playing_now == 'dam' else game

    def start_chosen():
        """Leave the chooser (or the name screen) for the game that was picked."""
        if chosen == 'dam':
            if not args.simulate and not snapshot.calibrated:
                return 'lobby', 'Calibrate the board before starting.'
            dam.names = dict(names)
            dam.new_match(ids[:2])  # Builder and attacker swap every round.
            return 'dam', ''
        if chosen in ('treasure', 'solo'):
            if not args.simulate and not snapshot.calibrated:
                return 'lobby', 'Calibrate the board before starting.'
            treasure.names = dict(names)
            if chosen == 'solo':
                treasure.new_match(ids[:1], (), solo_board, True)
                treasure.standings = scores.top(treasure.board['name'])
            else:
                treasure.new_match(ids[:2], (2,) if args.simulate else ())
            return 'treasure', ''
        if args.simulate:
            game.new_match(ids, walls)
            return 'game', ''
        return 'lobby', ''
    ids = list(range(1, config['game']['players'] + 1))
    # --simulate (and headless runs) never touch the hardware. Without it the camera and the controller
    # listener start as usual, and each frame is driven by lasers if any controller is connected or by
    # the mouse and keyboard if none is. From here on args.simulate means "this frame is mouse-driven".
    forced = args.simulate
    practice_walls = simulated_walls(width, height)
    walls = practice_walls if forced else None
    laser_at = None
    snapshot = VisionSnapshot()
    elapsed = 0.0
    frame_count = 0
    accumulator = 0.0
    selection = 0
    ready = set()
    previous = {}
    menu_presses = {}
    both_since = None
    disconnected_since = None
    countdown_until = 0.0
    projection_due = 0.0
    diagnostic_due = 0.0
    diagnostic_frame = 0
    diagnostic_time = time.monotonic()
    menu_message = ''
    status = ''
    preview = False
    running = True
    feedback_count = 0
    game.new_match(ids, walls)

    from beaver_battle.sound import SoundBoard
    sound = config.get('sound', {})
    board = SoundBoard(sound.get('enabled', True) and not args.mute and not args.headless, sound.get('volume', .42), sound.get('music_volume', .75) if sound.get('music', True) else 0)

    def text_line(text, y, large=False, color=None):
        # Everything the players read off the whiteboard is dark blue: it is what survives a projector.
        selected = text.startswith('> ')
        if large:
            surface = sprites.label(text, 84, sprites.BLUE_BRIGHT, tilt=2)
        else:
            surface = sprites.sign(text, 44 if selected else 36, color or sprites.BLUE)
        screen.blit(surface, ((width - surface.get_width()) // 2, y - (18 if large else 0)))

    def draw_home():
        """Pick a game: three cards, each showing a little of what it looks like."""
        home_rects.clear()
        screen.fill((224, 239, 241))
        text_line('PICK A GAME', 20, True)
        cards = (('BEAVER BATTLE', ('Canoes, rocks, power-ups.', 'Sink the other beaver.', '2 players'), 'water.jpg'),
                 ('TREASURE DASH', ('Draw a path to the chest,', 'then trace it to race there.', '1 or 2 players'), 'grass.jpg'),
                 ('DAM IT!', ('One draws a dam of sticks.', 'The other chews through it.', '2 players, swapping roles'), 'water.jpg'))
        for index, (title, lines, ground) in enumerate(cards):
            card = pygame.Rect(0, 0, 392, 420)
            card.center = (width // 2 + (index - 1) * 412, height // 2 + 30)
            home_rects.append(card)
            picked = index == home_pick
            pygame.draw.rect(screen, sprites.BLUE, card.inflate(20 if picked else 8, 20 if picked else 8), border_radius=38)
            pygame.draw.rect(screen, sprites.WHITE, card, border_radius=32)
            scene = pygame.Rect(card.x + 18, card.y + 18, card.width - 36, 190)
            view = sprites.tiled_ground(ground, scene.width, scene.height, 900 if index == 1 else 300, lighten=.35)
            if index == 0:
                for place, color, turn in (((95, 100), sprites.PLAYER_COLORS[0], 20), ((262, 88), sprites.PLAYER_COLORS[1], 165)):
                    boat = pygame.transform.rotozoom(sprites.canoe(18, color, 1.35), -turn, 1)
                    view.blit(boat, boat.get_rect(center=place))
                    head = sprites.tim(19, 1.35)
                    view.blit(head, head.get_rect(center=place))
                rock = sprites.pebble(5, zoom=2)
                view.blit(rock, rock.get_rect(center=(180, 94)))
            elif index == 1:
                trail = [(52, 138), (112, 108), (168, 140), (232, 104), (292, 100)]
                pygame.draw.lines(view, sprites.INK, False, trail, 13)
                pygame.draw.lines(view, sprites.PLAYER_COLORS[0], False, trail, 8)
                for piece, place in ((sprites.boulder(20, 3), (150, 70)), (sprites.chest(66), (316, 92)), (sprites.tim(22, 1.3), (58, 128))):
                    view.blit(piece, piece.get_rect(center=place))
            else:
                # The river held back by a line of sticks, the lodge dry behind it, and a beaver with its eye on the wood.
                meadow = sprites.tiled_ground('grass.jpg', scene.width, scene.height, 900, lighten=.35)
                view.blit(meadow, (150, 0), (150, 0, scene.width - 150, scene.height))
                pygame.draw.line(view, sprites.BARK_LINE, (150, -5), (162, scene.height + 5), 22)
                pygame.draw.line(view, sprites.BARK, (150, -5), (162, scene.height + 5), 14)
                for piece, place in ((sprites.lodge(52), (286, 100)), (sprites.tim(20, 1.25), (200, 116))):
                    view.blit(piece, piece.get_rect(center=place))
            screen.blit(view, scene)
            pygame.draw.rect(screen, sprites.BLUE, scene, 4, border_radius=6)
            name = sprites.label(title, 44, sprites.BLUE_BRIGHT)
            screen.blit(name, name.get_rect(center=(card.centerx, scene.bottom + 46)))
            for row, line in enumerate(lines):
                image = sprites.sign(line, 25, sprites.BLUE if row < 2 else sprites.BLUE_BRIGHT)
                screen.blit(image, image.get_rect(center=(card.centerx, scene.bottom + 96 + row * 34)))
        text_line('Click a game, or use the arrow keys and Enter' if args.simulate else 'Click a game, or button 1 to switch and button 2 to choose', height - 52)

    def draw_players():
        """Treasure Dash asks how many are playing: alone against the clock, or a race between two."""
        count_rects.clear()
        screen.fill((224, 239, 241))
        text_line('TREASURE DASH', 26, True)
        text_line('HOW MANY PLAYERS?', 150)
        options = (('1 PLAYER', ('Cross the whole board', 'against the clock.', "Today's best times are kept."), 1),
                   ('2 PLAYERS', ('Race from opposite edges', 'to the chest in the middle.', 'First beaver there wins.'), 2))
        for index, (title, lines, beavers) in enumerate(options):
            card = pygame.Rect(0, 0, 470, 330)
            card.center = (width // 2 + (index * 2 - 1) * 270, height // 2 + 50)
            count_rects.append(card)
            picked = index == count_pick
            pygame.draw.rect(screen, sprites.BLUE, card.inflate(20 if picked else 10, 20 if picked else 10), border_radius=40)
            pygame.draw.rect(screen, sprites.WHITE, card, border_radius=34)
            for number in range(beavers):
                face = game.portrait(number + 1, 2.2)
                screen.blit(face, face.get_rect(center=(card.centerx + (number * 2 - (beavers - 1)) * 62, card.y + 78)))
            name = sprites.label(title, 58, sprites.BLUE_BRIGHT)
            screen.blit(name, name.get_rect(center=(card.centerx, card.y + 168)))
            for row, line in enumerate(lines):
                image = sprites.sign(line, 26, sprites.BLUE if row < 2 else sprites.BLUE_BRIGHT)
                screen.blit(image, image.get_rect(center=(card.centerx, card.y + 222 + row * 34)))
        hover = scores_button.collidepoint(pygame.mouse.get_pos())
        scores_button.center = (width // 2, height - 96)
        pygame.draw.rect(screen, sprites.BLUE, scores_button.inflate(8, 8), border_radius=30)
        pygame.draw.rect(screen, sprites.BLUE if hover else sprites.WHITE, scores_button, border_radius=26)
        face = sprites.lettering("TODAY'S BEST TIMES  (B)", 28, sprites.WHITE if hover else sprites.BLUE, 1)
        screen.blit(face, face.get_rect(center=(scores_button.centerx, scores_button.centery - 2)))
        text_line('Click, press 1 or 2, or use the arrow keys and Enter.  Esc goes back.' if args.simulate
                  else 'Click, or button 1 to switch and button 2 to choose', height - 46)

    def draw_maps():
        """One player picks a map. Each card shows the map itself and today's time to beat on it."""
        map_rects.clear()
        screen.fill((224, 239, 241))
        text_line('PICK A MAP', 22, True)
        for index, layout in enumerate(SOLO_BOARDS):
            card = pygame.Rect(0, 0, 392, 400)
            card.center = (width // 2 + (index - 1) * 412, height // 2 + 40)
            map_rects.append(card)
            picked = index == map_pick
            pygame.draw.rect(screen, sprites.BLUE, card.inflate(20 if picked else 8, 20 if picked else 8), border_radius=38)
            pygame.draw.rect(screen, sprites.WHITE, card, border_radius=32)
            if index not in map_views:
                # The real board, shrunk: what you see is what you will play.
                sample = Treasure(config)
                sample.new_match((1,), (), index, solo=True)
                map_views[index] = pygame.transform.smoothscale(sample.ground(), (356, 200))
            scene = pygame.Rect(card.x + 18, card.y + 18, 356, 200)
            screen.blit(map_views[index], scene)
            pygame.draw.rect(screen, sprites.BLUE, scene, 4, border_radius=6)
            name = sprites.label(layout['name'], 40, sprites.BLUE_BRIGHT)
            screen.blit(name, name.get_rect(center=(card.centerx, scene.bottom + 44)))
            best = scores.top(layout['name'], 3)
            if not best:
                image = sprites.sign('No time yet today', 24, sprites.BLUE_BRIGHT)
                screen.blit(image, image.get_rect(center=(card.centerx, scene.bottom + 104)))
            for place, (name, seconds) in enumerate(best):
                image = sprites.sign(f'{place + 1}.  {name}   {seconds:.2f} s', 25 if place == 0 else 21, sprites.BLUE if place == 0 else sprites.BLUE_BRIGHT)
                screen.blit(image, image.get_rect(center=(card.centerx, scene.bottom + 90 + place * 30)))
        text_line('Click a map, press 1, 2 or 3, or use the arrow keys and Enter.  Esc goes back.' if args.simulate
                  else 'Click, or button 1 to switch and button 2 to choose', height - 46)

    def draw_scores():
        """Today's fastest one-player crossings, one leaderboard per map."""
        screen.fill((224, 239, 241))
        text_line("TODAY'S BEST TIMES", 22, True)
        for index, layout in enumerate(SOLO_BOARDS):
            panel = pygame.Rect(0, 0, 392, 420)
            panel.center = (width // 2 + (index - 1) * 412, height // 2 + 50)
            pygame.draw.rect(screen, sprites.BLUE, panel.inflate(8, 8), border_radius=36)
            pygame.draw.rect(screen, sprites.WHITE, panel, border_radius=32)
            title = sprites.label(layout['name'], 38, sprites.BLUE_BRIGHT)
            screen.blit(title, title.get_rect(center=(panel.centerx, panel.y + 48)))
            rows = scores.top(layout['name'], 8)
            if not rows:
                empty = sprites.sign('nobody yet', 26, sprites.BLUE_BRIGHT)
                screen.blit(empty, empty.get_rect(center=(panel.centerx, panel.y + 130)))
            for place, (name, seconds) in enumerate(rows):
                y = panel.y + 104 + place * 38
                for words, x, anchor in ((f'{place + 1}', panel.x + 36, 'center'), (name, panel.x + 70, 'midleft'), (f'{seconds:.2f} s', panel.right - 24, 'midright')):
                    image = sprites.lettering(words, 26, sprites.BLUE)
                    screen.blit(image, image.get_rect(**{anchor: (x, y)}))
        image = sprites.sign('One-player Treasure Dash times, wiped at midnight.  Press any key to go back.', 22, sprites.BLUE_BRIGHT)
        screen.blit(image, image.get_rect(center=(width // 2, 706)))

    def draw_info():
        """How to play, with the numbers read from the rules actually in force."""
        if resume_mode == 'treasure' and info_return == 'pause':
            draw_treasure_info()
            return
        if resume_mode == 'dam' and info_return == 'pause':
            draw_dam_info()
            return
        rules = config['game']
        screen.fill((224, 239, 241))
        text_line('HOW TO PLAY', 30, True)
        each = rules.get('reload_mode', 'each') == 'each'
        steer = ['Move the mouse: your canoe steers toward it', 'Left click: swimmer boost', 'Right click: use power-up or throw one rock',
                 'P or Esc: pause    R: new match    I: this page'] if args.simulate else [
                 'Point your laser: your canoe steers toward the dot', 'Hold button 1: aim the laser', 'Press button 2: use power-up or throw one rock',
                 'Hold both buttons for a second: pause']
        play = [f"You carry {rules['magazine']} rocks; " + (f"each comes back after {rules.get('reload_each', 1):g} s" if each else f"then wait {rules['reload_seconds']:g} s to reload"),
                'The first hit knocks you into the water']
        if rules.get('canoe_return', 0) > 0:
            play.append(f"Stay afloat {rules['canoe_return']:g} s and a new canoe arrives")
        play.append('Another hit' + (', or a canoe running you over,' if rules.get('ram_swimmers', True) else '') + ' sinks you')
        play.append((f"Sink a beaver: +1.  First to {rules['winning_score']} wins" if rules.get('scoring', 'kills') == 'kills'
                     else f"Last canoe afloat wins the round.  First to {rules['winning_score']} wins"))
        for column, (heading, lines) in enumerate((('CONTROLS', steer), ('RULES', play))):
            x = width // 4 + column * width // 2
            title = sprites.label(heading, 40, sprites.BLUE_BRIGHT)
            screen.blit(title, title.get_rect(center=(x, 186)))
            for index, line in enumerate(lines):
                image = sprites.sign(line, 25)
                screen.blit(image, image.get_rect(center=(x, 240 + index * 42)))
        title = sprites.label('POWER-UPS', 40, sprites.BLUE_BRIGHT)
        screen.blit(title, title.get_rect(center=(width // 2, 468)))
        for index, (kind, words) in enumerate((('laser', 'Laser: a beam straight ahead'), ('jouster', 'Jouster: ram with the horn'), ('mine', 'Mine: drop it and lure them in'))):
            x = width // 6 + index * width // 3
            icon = sprites.pickup(26, kind)
            screen.blit(icon, icon.get_rect(center=(x, 530)))
            image = sprites.sign(words, 25)
            screen.blit(image, image.get_rect(center=(x, 584)))
        for index, line in enumerate(('Break a drifting boulder to release a power-up, then sail close and it flies to you.',
                                      'Draw on the board with a marker: lines become sticks, closed shapes become logs and rocks.')):
            image = sprites.sign(line, 23)
            screen.blit(image, image.get_rect(center=(width // 2, 628 + index * 32)))
        image = sprites.sign('Press any key or button to go back', 22, sprites.BLUE_BRIGHT)
        screen.blit(image, image.get_rect(center=(width // 2, 700)))

    def draw_dam_info():
        rules = config['dam']
        screen.fill((224, 239, 241))
        text_line('HOW TO PLAY', 30, True)
        sections = (
            ('1  BUILD', (f"The builder has {rules['build_seconds']:g} s to draw a dam across the valley, from bank to bank",
                          'Hold the left button to draw; right click when you are done' if args.simulate else 'Draw it on the whiteboard with a marker',
                          'One clean wall is the strongest dam: extra or thick wood makes the whole dam weaker and quicker to chew',
                          'Open strokes are sticks and closed shapes are logs. A real gap leaks, and a leak loses at once')),
            ('2  ATTACK', (('The beaver follows the mouse; click or hold next to the wood to chew it' if args.simulate
                            else 'The beaver follows your laser; press a button next to the wood to chew it'),
                           'A broken piece lets the river through, and the water runs for the lodge',
                           f"Flood the lodge within {rules['attack_seconds']:g} s and the attacker wins; keep it dry and the builder wins",
                           'Then swap roles and play again')),
        )
        y = 170
        for heading, lines in sections:
            title = sprites.label(heading, 40, sprites.BLUE_BRIGHT)
            screen.blit(title, title.get_rect(center=(width // 2, y)))
            for index, line in enumerate(lines):
                image = sprites.sign(line, 25)
                screen.blit(image, image.get_rect(center=(width // 2, y + 52 + index * 40)))
            y += 84 + len(lines) * 40
        image = sprites.sign('Press any key or button to go back', 22, sprites.BLUE_BRIGHT)
        screen.blit(image, image.get_rect(center=(width // 2, 700)))

    def draw_treasure_info():
        rules = config['treasure']
        screen.fill((224, 239, 241))
        text_line('HOW TO PLAY', 30, True)
        sections = (
            ('1  DRAW', (f"You have {rules['draw_seconds']:g} s to draw a path from your corner to the chest",
                         'Hold the left button to draw; right click when you are done' if args.simulate else 'Draw it on the whiteboard with a marker',
                         f"Small breaks (up to {rules['merge_gap']:g} px) are joined up for you",
                         'A path that never reaches the chest loses on the spot')),
            ('2  RACE', ('Your beaver only walks while your laser traces the line just ahead of it' if not args.simulate
                         else 'Your beaver only walks while the mouse traces the line just ahead of it',
                         f"Rocks, trees and sticks on your line stop you for good; running into a deer winds you for {rules.get('deer_stall', 2):g} s",
                         'Ponds are allowed: you swim a bit slower, so a short swim can beat a long detour',
                         ('Click' if args.simulate else 'Either button') + f": a {rules['boost_seconds']:g} s speed boost, ready again after {rules['boost_cooldown']:g} s",
                         'Matching portals join up: draw to one and carry on from the other. Berries give a burst of speed',
                         'First beaver to the chest wins' if not (treasure.solo) else "Reach the chest as fast as you can: today's best times are kept per board")),
        )
        y = 170
        for heading, lines in sections:
            title = sprites.label(heading, 40, sprites.BLUE_BRIGHT)
            screen.blit(title, title.get_rect(center=(width // 2, y)))
            for index, line in enumerate(lines):
                image = sprites.sign(line, 26)
                screen.blit(image, image.get_rect(center=(width // 2, y + 52 + index * 40)))
            y += 78 + len(lines) * 40
        image = sprites.sign('Press any key or button to go back', 22, sprites.BLUE_BRIGHT)
        screen.blit(image, image.get_rect(center=(width // 2, 700)))

    try:
        if not forced:
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
                if getattr(event, 'laser_mode', mode) != mode:
                    continue
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN and mode == 'names' and event.key != pygame.K_ESCAPE:
                    # Typing a name: every key is a letter here, not a menu shortcut.
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
                        if typed.strip():
                            names[ids[naming]] = typed.strip()
                        naming, typed = naming + 1, ''
                        if naming >= len(ids):
                            game.names, naming = dict(names), 0
                            if chosen in ('treasure', 'solo', 'dam'):
                                mode, menu_message = start_chosen()
                            elif args.simulate:
                                game.new_match(ids, walls)
                                mode = 'game'
                            else:
                                ready, mode = set(), 'ready'
                    elif event.key == pygame.K_BACKSPACE:
                        typed = typed[:-1]
                    elif event.unicode and event.unicode.isprintable() and len(typed) < 10:
                        typed = (typed + event.unicode.upper()).lstrip()
                    continue
                if (event.type == pygame.KEYDOWN and event.key == pygame.K_q and
                        mode in ('game', 'treasure', 'dam', 'pause', 'countdown', 'ready')):
                    mode, selection, accumulator = 'home', 0, 0.0
                    ready, again, menu_message, status = set(), False, '', ''
                    continue
                if mode == 'scores':
                    if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                        mode = 'players'
                    continue
                if mode == 'players':
                    decided = False
                    if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
                        for index, rect in enumerate(count_rects):
                            if rect.collidepoint(event.pos):
                                count_pick = index
                                decided = event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and scores_button.collidepoint(event.pos):
                            mode = 'scores'
                            continue
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            mode = 'home'
                            continue
                        if event.key == pygame.K_b:
                            mode = 'scores'
                            continue
                        if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, pygame.K_TAB):
                            count_pick = 1 - count_pick
                        if event.key in (pygame.K_1, pygame.K_KP1, pygame.K_2, pygame.K_KP2):
                            count_pick, decided = (0 if event.key in (pygame.K_1, pygame.K_KP1) else 1), True
                        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            decided = True
                    if decided:
                        chosen = ('solo', 'treasure')[count_pick]
                        ids = [1] if chosen == 'solo' else [1, 2]
                        naming, typed = 0, ''
                        mode, menu_message = ('maps', '') if chosen == 'solo' else ('names', '') if ask_names else start_chosen()
                    if event.type != pygame.QUIT:
                        continue
                if mode == 'maps':
                    # One player chooses among three maps; each keeps its own leaderboard.
                    decided = False
                    if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
                        for index, rect in enumerate(map_rects):
                            if rect.collidepoint(event.pos):
                                map_pick = index
                                decided = event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            mode = 'players'
                            continue
                        if event.key in (pygame.K_LEFT, pygame.K_UP):
                            map_pick = (map_pick - 1) % len(SOLO_BOARDS)
                        if event.key in (pygame.K_RIGHT, pygame.K_DOWN, pygame.K_TAB):
                            map_pick = (map_pick + 1) % len(SOLO_BOARDS)
                        if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_KP1, pygame.K_KP2, pygame.K_KP3):
                            map_pick, decided = {pygame.K_1: 0, pygame.K_KP1: 0, pygame.K_2: 1, pygame.K_KP2: 1}.get(event.key, 2), True
                        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            decided = True
                    if decided:
                        solo_board, chosen, ids, naming, typed = map_pick, 'solo', [1], 0, ''
                        mode, menu_message = ('names', '') if ask_names and not names.get(1) else start_chosen()
                    if event.type != pygame.QUIT:
                        continue
                if mode == 'home':
                    picked = False
                    if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
                        for index, rect in enumerate(home_rects):
                            if rect.collidepoint(event.pos):
                                home_pick = index
                                picked = event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                    if event.type == pygame.KEYDOWN and event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_DOWN, pygame.K_UP, pygame.K_TAB):
                        home_pick = (home_pick + (-1 if event.key in (pygame.K_LEFT, pygame.K_UP) else 1)) % len(games)
                    if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        picked = True
                    if picked:
                        chosen = games[home_pick]
                        if chosen == 'treasure':
                            mode = 'players'
                        elif chosen == 'dam':
                            ids, naming, typed = [1, 2], 0, ''
                            mode, menu_message = ('names', '') if ask_names else start_chosen()
                        elif args.simulate:
                            ids = list(range(1, config['game']['players'] + 1))
                            naming, typed = 0, ''
                            mode, menu_message = ('names', '') if ask_names else start_chosen()
                        else:
                            mode, selection = 'lobby', 0
                    if event.type != pygame.QUIT and not (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                        continue
                if mode == 'info' and event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                    mode = info_return
                    continue
                if mode in ('lobby', 'pause') and event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
                    # The menu is a column of real buttons: pointing at one selects it, clicking chooses it.
                    for index, rect in enumerate(menu_rects):
                        # Include the visible four-pixel outline in the hit target.
                        if rect.inflate(8, 8).collidepoint(event.pos):
                            selection = index
                            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                                key_confirm = True
                playing = mode in ('game', 'treasure', 'dam')
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and playing and active_game().phase == 'match_over':
                    if again_button.collidepoint(event.pos):
                        again = True
                    elif mode == 'treasure' and treasure.solo and next_button.collidepoint(event.pos):
                        mode, map_pick = 'maps', treasure.board_index
                        continue
                    elif menu_button.collidepoint(event.pos):
                        mode, selection = ('home' if mode in ('treasure', 'dam') else 'lobby'), 0
                        continue
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and playing and active_game().phase == 'match_over':
                    again = True
                    continue
                if event.type == pygame.KEYDOWN and event.key == pygame.K_n and mode == 'treasure' and treasure.solo and treasure.phase == 'match_over':
                    mode, map_pick = 'maps', treasure.board_index
                    continue
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and (playing or mode == 'pause'):
                    if pause_button.collidepoint(event.pos):
                        if playing:
                            resume_mode, mode, selection = mode, 'pause', 0
                        else:
                            mode = resume_mode
                        continue
                    elif info_button.collidepoint(event.pos):
                        if playing:
                            resume_mode = mode
                        mode, info_return = 'info', 'pause'
                        continue
                if event.type == pygame.KEYDOWN and event.key == pygame.K_p and (playing or mode == 'pause'):
                    if playing:
                        resume_mode, mode, selection = mode, 'pause', 0
                    else:
                        mode = resume_mode
                    continue
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_i, pygame.K_F1) and (playing or mode in ('pause', 'lobby')):
                    if playing:
                        resume_mode = mode
                    mode, info_return = 'info', 'lobby' if mode == 'lobby' else 'pause'
                    continue
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if mode in ('game', 'treasure', 'dam'):
                            resume_mode, mode = mode, 'pause'
                            selection = 0
                        elif mode == 'pause':
                            mode = resume_mode
                        elif mode == 'home':
                            running = False
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
                    elif event.key == pygame.K_c and not forced:
                        mode = 'calibration'
                        vision.begin_calibration()
                    elif event.key == pygame.K_r and args.simulate and mode in ('game', 'treasure', 'dam', 'pause'):
                        mode, menu_message = start_chosen()
            laser_at = None
            if forced:
                mouse = pygame.mouse.get_pos() if args.mouse else None
                pressed = pygame.mouse.get_pressed(3)
                if pause_button.union(info_button).collidepoint(pygame.mouse.get_pos()):
                    pressed = (False, False, False)  # A click on a button is not a throw.
                inputs = simulated_inputs(config, elapsed, mouse, (pressed[0], pressed[2]), ids)
                active_ids = ids
            elif args.bench:
                snapshot = vision.snapshot()
                walls = snapshot.walls
                active_ids = list(range(1, args.bench + 1))
                inputs = simulated_inputs(config, elapsed, None, (False, False), active_ids)
            else:
                bridge.poll(now)
                active_ids = bridge.active_ids(now)
                scheduler.update(now, bridge, vision, mode != 'calibration')
                snapshot = vision.snapshot()
                walls = snapshot.walls
                inputs = bridge.inputs(snapshot, now)
                bridge.send(now)
                args.simulate = not active_ids and (args.simulate or mode not in ('game', 'pause', 'countdown', 'ready', 'survey_match'))
                if args.simulate:
                    # No laser controller is connected: the mouse steers player 1 and the keyboard works the
                    # menus, exactly as under --simulate, with practice walls if the camera has none to offer.
                    pressed = pygame.mouse.get_pressed(3)
                    if pause_button.union(info_button).collidepoint(pygame.mouse.get_pos()):
                        pressed = (False, False, False)
                    inputs = simulated_inputs(config, elapsed, pygame.mouse.get_pos(), (pressed[0], pressed[2]), ids)
                    active_ids = ids
                    walls = walls if snapshot.calibrated and walls is not None else practice_walls
                else:
                    pointer = inputs.get(min(active_ids, default=1))
                    if pointer is not None and pointer.aim is not None:
                        laser_at = (round(pointer.aim[0]), round(pointer.aim[1]))
                    if laser_at is not None and mode in ('home', 'players', 'maps', 'lobby', 'pause', 'scores', 'info'):
                        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=laser_at, rel=(0, 0), buttons=(0, 0, 0)))
            host = min(active_ids, default=1)
            host_input = inputs.get(host, PlayerInput(host, connected=False))
            old_fire, old_special = previous.get(host, (False, False))
            driven = args.simulate or args.bench
            cycle = key_cycle or (not driven and host_input.fire and not old_fire)
            confirm = key_confirm or (not driven and host_input.special and not old_special)
            pointer_menu = mode in ('home', 'players', 'maps', 'lobby', 'pause', 'scores', 'info')
            if pointer_menu and not driven:
                # FIRE illuminates the pointer; it must not also change selection.
                cycle, confirm = key_cycle, key_confirm
                for player_id in sorted(active_ids):
                    control = inputs.get(player_id)
                    was_special = previous.get(player_id, (False, False))[1]
                    if control and control.connected and (control.special_pressed or (control.special and not was_special)):
                        menu_presses[player_id] = (mode, now + 0.3)
                clicked = False
                for player_id, (pressed_mode, deadline) in list(menu_presses.items()):
                    control = inputs.get(player_id)
                    if pressed_mode != mode or now > deadline or not control or not control.connected:
                        del menu_presses[player_id]
                    elif control.aim is not None:
                        del menu_presses[player_id]
                        if clicked:
                            continue
                        position = tuple(round(value) for value in control.aim)
                        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=position, rel=(0, 0), buttons=(0, 0, 0)))
                        pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=position, button=1, laser_mode=mode))
                        clicked = True
            else:
                menu_presses.clear()
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
            if mode == 'home' and (cycle or (confirm and not key_confirm)):
                if cycle:
                    home_pick = (home_pick + 1) % len(games)
                else:
                    chosen = games[home_pick]
                    if chosen == 'dam':
                        ids, naming, typed = [1, 2], 0, ''
                    mode, menu_message = ('players', '') if chosen == 'treasure' else (('names', '') if ask_names else start_chosen()) if chosen == 'dam' else ('lobby', '')
            elif mode == 'players' and (cycle or (confirm and not key_confirm)):
                if cycle:
                    count_pick = 1 - count_pick
                else:
                    chosen = ('solo', 'treasure')[count_pick]
                    ids = [1] if chosen == 'solo' else [1, 2]
                    naming, typed = 0, ''
                    mode, menu_message = ('maps', '') if chosen == 'solo' else ('names', '') if ask_names else start_chosen()
            elif mode == 'maps' and (cycle or (confirm and not key_confirm)):
                if cycle:
                    map_pick = (map_pick + 1) % len(SOLO_BOARDS)
                else:
                    solo_board, chosen, ids, naming, typed = map_pick, 'solo', [1], 0, ''
                    mode, menu_message = ('names', '') if ask_names and not names.get(1) else start_chosen()
            if mode in ('lobby', 'pause'):
                choices = menu_choices(mode)
                if cycle:
                    selection = (selection + 1) % len(choices)
                if confirm:
                    choice = choices[selection]
                    if choice == 'Quit':
                        running = False
                    elif choice == 'Calibrate board':
                        if forced:
                            menu_message = 'Calibration uses the real USB camera.'
                        else:
                            mode = 'calibration'
                            vision.begin_calibration()
                    elif choice == 'How to play':
                        mode, info_return, info_frame = 'info', mode, frame_count
                    elif choice == 'Change game':
                        mode = 'home'
                    elif choice == 'Resume':
                        mode, status = resume_mode, ''
                    elif choice == 'New match' and resume_mode in ('treasure', 'dam'):
                        mode, menu_message = start_chosen()
                    elif choice == 'New match':
                        mode, selection = 'lobby', 0
                    else:
                        count = 2
                        if len(active_ids) < count:
                            menu_message = f'Waiting for {count} controllers; {len(active_ids)} connected.'
                        elif not args.simulate and not snapshot.calibrated:
                            menu_message = 'Calibrate the board before starting.'
                        else:
                            ids, ready, mode = active_ids[:count], set(), 'names' if ask_names else 'ready'
                            naming, typed, menu_message = 0, '', ''
            elif mode == 'ready':
                for player_id in ids:
                    current = inputs.get(player_id, PlayerInput(player_id, connected=False))
                    if current.special and not previous.get(player_id, (False, False))[1]:
                        ready.add(player_id)
                if key_confirm or args.simulate:
                    ready.update(ids)
                if all(player_id in ready and player_id in active_ids for player_id in ids):
                    if args.simulate:
                        game.new_match(ids, walls)
                        mode, countdown_until = 'countdown', elapsed + 3.0
                    else:
                        vision.begin_survey()
                        mode = 'survey_match'
            elif mode == 'survey_match':
                if not vision.surveying():
                    snapshot = vision.snapshot()
                    walls = snapshot.walls
                    game.new_match(ids, walls)
                    mode, countdown_until = 'countdown', elapsed + 3.0
            elif mode == 'countdown':
                game.update(0, {})  # Install the fixed board while the countdown runs.
                if not args.bench and any(player_id not in active_ids for player_id in ids):
                    mode, ready = 'ready', set()
                elif elapsed >= countdown_until:
                    mode, accumulator = 'game', 0.0
            elif mode == 'calibration' and snapshot.calibrated:
                mode, selection, menu_message = 'lobby', 0, 'Calibration saved.'
            if args.bench and mode == 'lobby' and snapshot.calibrated:
                ids = active_ids
                vision.begin_survey()
                mode, menu_message = 'survey_match', ''
            missing_controller = mode == 'game' and not args.simulate and not args.bench and any(
                player_id not in active_ids for player_id in ids)
            if missing_controller:
                disconnected_since = now if disconnected_since is None else disconnected_since
            else:
                disconnected_since = None
            if mode == 'game' and not args.simulate:
                if missing_controller and now - disconnected_since >= 3.0:
                    mode, selection, status = 'pause', 0, 'Controller disconnected. Reconnect, then resume.'
                elif now - snapshot.timestamp > config['camera']['stale_seconds'] or not snapshot.calibrated:
                    mode, selection, status = 'pause', 0, 'Camera tracking unavailable. Restore camera or calibrate.'
            if mode == 'game':
                # Freeze combat during a brief reboot; discard elapsed time rather than catch up.
                accumulator = 0 if missing_controller else accumulator + dt
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
                if game.phase == 'match_over' and (again or (cycle and not key_cycle)):
                    # Play again: the same players and names, straight into a new match.
                    if args.simulate:
                        game.new_match(ids, walls)
                        mode, accumulator = 'game', 0.0
                    else:
                        vision.begin_survey()
                        mode, accumulator = 'survey_match', 0.0
                elif game.phase == 'match_over' and confirm:
                    mode, selection = 'lobby', 0
                again = False
            elif mode == 'treasure':
                accumulator += dt
                if args.simulate:
                    spot, held = pygame.mouse.get_pos(), pygame.mouse.get_pressed(3)
                    if pause_button.union(info_button).collidepoint(spot):
                        held = (False, False, False)
                    inputs = {1: PlayerInput(1, spot, held[0], held[2])}
                while accumulator >= 1 / 60:
                    # On a laptop the cursor is the pen; on the board the camera's marker mask is the ink.
                    treasure.update(1 / 60, inputs, None if args.simulate else snapshot.walls)
                    board.play(treasure.sounds)
                    treasure.sounds.clear()
                    accumulator -= 1 / 60
                if treasure.phase != 'match_over':
                    recorded = False
                elif treasure.solo and treasure.winner is not None and not recorded:
                    # A finished solo run goes on today's board for this layout, once.
                    recorded, solo_board = True, treasure.board_index
                    treasure.rank = scores.record(treasure.board['name'], treasure.name(treasure.winner), treasure.race_time)
                    treasure.standings = scores.top(treasure.board['name'])
                if treasure.phase == 'match_over' and (again or (cycle and not key_cycle)):
                    mode, menu_message = start_chosen()
                elif treasure.phase == 'match_over' and confirm:
                    mode, selection = 'home', 0
                again = False
            elif mode == 'dam':
                accumulator += dt
                if args.simulate:
                    # One mouse, two jobs: whoever's turn it is holds it. The builder draws, then passes it over.
                    spot, held = pygame.mouse.get_pos(), pygame.mouse.get_pressed(3)
                    if pause_button.union(info_button).collidepoint(spot):
                        held = (False, False, False)
                    inputs = {player_id: PlayerInput(player_id, spot, held[0], held[2]) for player_id in (1, 2)}
                while accumulator >= 1 / 60:
                    dam.update(1 / 60, inputs, None if args.simulate else snapshot.walls)
                    board.play(dam.sounds)
                    dam.sounds.clear()
                    accumulator -= 1 / 60
                if dam.phase == 'match_over' and (again or (cycle and not key_cycle)):
                    mode, menu_message = start_chosen()
                elif dam.phase == 'match_over' and confirm:
                    mode, selection = 'home', 0
                again = False
            else:
                accumulator = 0
            if mode in ('survey', 'survey_match'):
                # No text, progress bars, or cursor rings: only physical ink is scanned.
                screen.fill((255, 255, 255))
            elif mode == 'calibration':
                vision.draw_calibration(screen)
                # Name the corner that is blocked, on the board, where the operator is standing.
                text_line(snapshot.error or 'Keep all four markers in view', height // 2 + 60)
                text_line('Keep ink and hands out of the four corners. Hold both buttons to cancel.', height // 2 + 96)
            elif mode == 'info':
                # The press that opened this page must not also be the press that closes it.
                if (cycle or confirm) and frame_count > info_frame:
                    mode = info_return
                draw_info()
            elif mode == 'home':
                draw_home()
            elif mode == 'scores':
                draw_scores()
            elif mode == 'players':
                draw_players()
            elif mode == 'maps':
                draw_maps()
            elif mode in ('game', 'countdown', 'treasure', 'dam'):
                active_game().draw(screen)
                if mode in ('game', 'treasure', 'dam') and active_game().phase == 'match_over':
                    solo_over = mode == 'treasure' and treasure.solo
                    # Solo gets three: the same board again, the next board, or out.
                    again_button.center = (width // 2 - (330 if solo_over else 170), height - 96)
                    next_button.center = (width // 2, height - 96)
                    menu_button.center = (width // 2 + (300 if solo_over else 210), height - 96)
                    hints = [('RETRY' if solo_over else 'SWAP ROLES' if mode == 'dam' else 'PLAY AGAIN', 'Enter' if args.simulate else 'button 1', again_button)]
                    if solo_over:
                        hints.append(('CHANGE MAP', 'N', next_button))
                    hints.append(('MENU', 'Space' if args.simulate else 'button 2', menu_button))
                    for words, how, button in hints:
                        hover = button.collidepoint(pygame.mouse.get_pos())
                        pygame.draw.rect(screen, sprites.WHITE, button.inflate(10, 10), border_radius=36)
                        pygame.draw.rect(screen, sprites.BLUE_BRIGHT if hover else sprites.BLUE, button, border_radius=32)
                        face = sprites.lettering(words, 38, sprites.WHITE, 2)
                        screen.blit(face, face.get_rect(center=(button.centerx, button.centery - 2)))
                        hint = sprites.sign(how, 22)
                        screen.blit(hint, hint.get_rect(center=(button.centerx, button.bottom + 20)))
                if mode in ('game', 'treasure', 'dam'):
                    for button, icon in ((pause_button, 'pause'), (info_button, 'info')):
                        pygame.draw.circle(screen, sprites.BLUE, button.center, 20)
                        pygame.draw.circle(screen, sprites.WHITE, button.center, 16)
                        if icon == 'pause':
                            for x in (-6, 3):
                                pygame.draw.rect(screen, sprites.BLUE, (button.centerx + x, button.centery - 8, 4, 16), border_radius=2)
                        else:
                            mark = sprites.lettering('i', 26, sprites.BLUE)
                            screen.blit(mark, mark.get_rect(center=(button.centerx, button.centery - 1)))
                if mode == 'countdown':
                    text_line(str(max(1, math.ceil(countdown_until - elapsed))), height // 2 - 32, True)
                    text_line('Hold both buttons to cancel', height - 45)
            else:
                screen.fill((224, 239, 241))
                text_line('BEAVER BATTLE', 80, True)
                if mode == 'names':
                    text_line("WHO'S PLAYING?", 196)
                    for index, player_id in enumerate(ids):
                        y, active = 300 + index * 105, index == naming
                        face = game.portrait(player_id, 2.4 if active else 1.7)
                        if active:
                            # What has been typed, a blinking bar, and a pale hint of what Enter alone would keep.
                            line = sprites.sign(typed, 64) if typed else sprites.sign(names.get(player_id, f'PLAYER {player_id}'), 64, sprites.tint(sprites.BLUE, .62))
                        else:
                            line = sprites.sign(names.get(player_id, f'PLAYER {player_id}'), 44)
                        left = (width - face.get_width() - 28 - line.get_width()) // 2
                        screen.blit(face, (left, y - face.get_height() // 2))
                        screen.blit(line, (left + face.get_width() + 28, y - line.get_height() // 2))
                        if active and int(elapsed * 2) % 2:
                            bar = left + face.get_width() + 28 + (line.get_width() + 6 if typed else 0)
                            pygame.draw.rect(screen, sprites.BLUE, (bar, y - 30, 7, 60), border_radius=3)
                elif mode == 'ready':
                    text_line('Press button 2 to ready', 180)
                    for index, player_id in enumerate(ids):
                        text_line(f'{game.name(player_id)}: {"READY" if player_id in ready else "waiting"}', 260 + index * 55)
                else:
                    menu_rects = []
                    for index, choice in enumerate(menu_choices(mode)):
                        button = pygame.Rect(0, 0, 470, 54)
                        button.center = (width // 2, 250 + index * 64)
                        menu_rects.append(button)
                        chosen = index == selection
                        pygame.draw.rect(screen, sprites.BLUE, button.inflate(8, 8), border_radius=32)
                        pygame.draw.rect(screen, sprites.BLUE if chosen else sprites.WHITE, button, border_radius=28)
                        face = sprites.lettering(choice, 34, sprites.WHITE if chosen else sprites.BLUE, 1)
                        screen.blit(face, face.get_rect(center=(button.centerx, button.centery - 2)))
                text_line(menu_message or status or ('Type a name, then Enter.  Enter alone keeps the one shown.' if mode == 'names' else
                                                     'Hold both buttons to cancel' if mode == 'ready' else ('Click a button, or press Down then Enter' if args.simulate else 'Hold button 1 to aim; hover a button and press button 2')), height - 100)
                camera_status = 'simulated' if forced else (snapshot.error or ('calibrated' if snapshot.calibrated else 'needs calibration'))
                lasers = 'no lasers connected: using mouse and keyboard' if args.simulate and not forced else f'Connected: {active_ids}'
                text_line(f'{lasers}    Camera: {camera_status}', height - 55)
            if preview and mode in ('lobby', 'pause') and snapshot.preview is not None:
                import cv2
                image = cv2.cvtColor(snapshot.preview, cv2.COLOR_BGR2RGB)
                image = pygame.surfarray.make_surface(np.transpose(image, (1, 0, 2)))
                screen.blit(pygame.transform.smoothscale(image, (width // 4, height // 4)), (width * 3 // 4, 0))
            if not forced and not args.bench and mode not in ('calibration', 'survey', 'survey_match'):
                for player_id in active_ids:
                    control = inputs.get(player_id)
                    if control is None or not control.connected or control.aim is None:
                        continue
                    position = tuple(round(value) for value in control.aim)
                    color = (35, 90, 220) if player_id == 1 else (20, 155, 115)
                    pygame.draw.circle(screen, color, position, 19, 4)
                    pygame.draw.circle(screen, sprites.WHITE, position, 14, 2)
                    label = sprites.lettering(str(player_id), 20, color, 1)
                    screen.blit(label, (position[0] + 23, position[1] - 12))
            pygame.display.flip()
            # Vision needs what we just projected to tell a shadow from dark artwork.
            # Reading the framebuffer costs real time, so only at the wall update rate.
            if not forced and mode != 'calibration' and now >= projection_due:
                projection_due = now + 1 / max(1.0, float(config['camera'].get('wall_update_hz', 10)))
                vision.set_projection(np.transpose(pygame.surfarray.array3d(screen), (1, 0, 2)), now)
            if args.diagnostics_dir and now >= diagnostic_due:
                import cv2
                args.diagnostics_dir.mkdir(parents=True, exist_ok=True)
                if snapshot.preview is not None:
                    cv2.imwrite(str(args.diagnostics_dir / 'camera.jpg'), snapshot.preview)
                if snapshot.walls is not None:
                    cv2.imwrite(str(args.diagnostics_dir / 'walls.png'), snapshot.walls.astype(np.uint8) * 255)
                if game.match_walls is not None:
                    cv2.imwrite(str(args.diagnostics_dir / 'match-ink.png'), game.match_walls.astype(np.uint8) * 255)
                if game.walls is not None:
                    cv2.imwrite(str(args.diagnostics_dir / 'match-solid.png'), game.walls.astype(np.uint8) * 255)
                diagnostic = dict(mode=mode, calibrated=snapshot.calibrated, error=snapshot.error,
                    controllers=active_ids, aims=dict(snapshot.aims),
                    confidence=dict(snapshot.confidence),
                    frame_age=max(0.0, now - snapshot.timestamp),
                    buttons={player: dict(fire=value.fire, special=value.special)
                             for player, value in inputs.items()},
                    wall_fraction=float(snapshot.walls.mean()) if snapshot.walls is not None else None,
                    fps=(frame_count - diagnostic_frame) / max(.001, now - diagnostic_time))
                (args.diagnostics_dir / 'status.json').write_text(json.dumps(diagnostic))
                diagnostic_due, diagnostic_time, diagnostic_frame = now + 1, now, frame_count
            previous = {player_id: (value.fire, value.special) for player_id, value in inputs.items()}
            if args.seconds and elapsed >= args.seconds:
                running = False
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            pygame.image.save(screen, str(args.screenshot))
        report = dict(frames=frame_count, simulation_seconds=round(elapsed, 3), mode=mode, phase=game.phase,
                      scores=game.scores, feedback_events=feedback_count, simulated=bool(args.simulate),
                      projector=config['projector'], display_index=display_index,
                      camera_calibrated=bool(snapshot.calibrated),
                      camera_frame_available=snapshot.preview is not None)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    finally:
        if game.geometry is not None:
            game.geometry.stop()
        bridge.stop()
        if not forced:
            vision.stop()
        pygame.quit()
