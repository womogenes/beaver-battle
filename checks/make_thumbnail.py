"""Draw the project thumbnail from the game's own art: python checks/make_thumbnail.py  ->  docs/thumbnail.png"""

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame

from beaver_battle import sprites

WIDTH, HEIGHT = 1280, 640  # GitHub's social preview shape.


def put(surface, image, center, degrees=0):
    if degrees:
        image = pygame.transform.rotozoom(image, degrees, 1)
    surface.blit(image, image.get_rect(center=center))


def burst(surface, center, reach, color, twist=.3):
    for fill, size in ((sprites.INK, reach + 8), (color, reach), (sprites.WHITE, reach * .55)):
        points = [(center[0] + size * (1 if index % 2 else .52) * math.cos(twist + index * math.tau / 20),
                   center[1] + size * (1 if index % 2 else .52) * math.sin(twist + index * math.tau / 20)) for index in range(20)]
        pygame.draw.polygon(surface, fill, points)


def laser(surface, source, target):
    """A laser pointer's beam and the dot where it lands. Red is fine here: a thumbnail is never projected."""
    glow = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    for wide, shade in ((26, (255, 60, 70, 38)), (14, (255, 60, 70, 80)), (6, (255, 90, 100, 230)), (2, (255, 235, 235, 255))):
        pygame.draw.line(glow, shade, source, target, wide)
    for reach, shade in ((34, (255, 60, 70, 50)), (22, (255, 60, 70, 110)), (13, (255, 70, 80, 255)), (6, (255, 240, 240, 255))):
        pygame.draw.circle(glow, shade, target, reach)
    surface.blit(glow, (0, 0))


def main():
    pygame.init()
    pygame.display.set_mode((1, 1))
    blue, yellow, purple = sprites.PLAYER_COLORS
    image = sprites.tiled_ground("water.jpg", WIDTH, HEIGHT, 640, lighten=.3)
    # The meadow of Treasure Dash comes in on a slant from the right.
    meadow = sprites.tiled_ground("grass.jpg", WIDTH, HEIGHT, WIDTH, lighten=.3)
    mask = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    shore = [(760, 0), (WIDTH, 0), (WIDTH, HEIGHT), (610, HEIGHT)]
    pygame.draw.polygon(mask, (255, 255, 255, 255), shore)
    meadow = meadow.convert_alpha()
    meadow.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pygame.draw.line(image, (226, 214, 160), shore[0], shore[3], 46)
    image.blit(meadow, (0, 0))
    pygame.draw.line(image, sprites.INK, (748, 0), (598, HEIGHT), 6)

    # Beaver Battle: two canoes, a rock in the air, and a hit landing.
    for place, color, heading, zoom in (((200, 475), blue, 24, 3.0), ((585, 395), yellow, 160, 2.5)):
        put(image, sprites.canoe(18, color, zoom), place, heading)
        put(image, sprites.tim(16, zoom), place)
    burst(image, (468, 352), 62, blue)
    put(image, sprites.pebble(5, zoom=5), (372, 405), 25)
    put(image, sprites.pebble(5, zoom=4), (316, 432), 80)
    put(image, sprites.lily_pad(16, 2.0, 40), (70, 330))
    put(image, sprites.lily_pad(16, 1.7, 200, flower=False), (450, 575))
    put(image, sprites.pickup(14, "laser", 2.2), (610, 560))

    # Treasure Dash: a drawn path round a rock and a pond to the chest.
    pond = pygame.Rect(0, 0, 250, 150)
    pond.center = (1075, 350)
    pygame.draw.ellipse(image, (226, 214, 160), pond.inflate(22, 22))
    water = sprites.tiled_ground("water.jpg", pond.width, pond.height, 360, lighten=.15).convert_alpha()
    oval = pygame.Surface(pond.size, pygame.SRCALPHA)
    pygame.draw.ellipse(oval, (255, 255, 255, 255), oval.get_rect())
    water.blit(oval, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    image.blit(water, pond)
    trail = [(800, 560), (880, 470), (960, 500), (1030, 450), (1000, 400), (1100, 470), (1170, 520), (1200, 540)]
    for wide, shade in ((20, sprites.INK), (12, purple)):
        pygame.draw.lines(image, shade, False, trail, wide)
        for point in trail:
            pygame.draw.circle(image, shade, point, wide // 2)
    put(image, sprites.boulder(22, 3, 2.2), (905, 545))
    put(image, sprites.boulder(22, 5, 1.6), (1010, 560))
    put(image, sprites.chest(62, 2.2, open_lid=True), (1200, 500))
    put(image, sprites.tim(19, 2.8), (800, 545))

    # One pointer steers a canoe, the other traces a path.
    laser(image, (-20, 330), (352, 300))
    laser(image, (WIDTH + 20, 610), (902, 470))

    title = sprites.label("BEAVER BATTLES", 142, sprites.BLUE_BRIGHT, tilt=3)
    put(image, title, (WIDTH // 2, 112))
    tag = sprites.label("ON YOUR WHITEBOARD", 60, (255, 205, 70), tilt=3)
    put(image, tag, (WIDTH // 2 + 150, 232))

    out = Path(__file__).resolve().parents[1] / "docs" / "thumbnail.png"
    pygame.image.save(image, out)
    print(out)


if __name__ == "__main__":
    main()
