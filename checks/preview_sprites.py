"""Render a labelled sprite sheet and a mock scene: python checks/preview_sprites.py OUTPUT_DIR"""

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame

from beaver_battle import sprites

pygame.init()
pygame.display.set_mode((1, 1))
fonts = {}


def font(size):
    if size not in fonts:
        fonts[size] = pygame.font.Font(None, size)
    return fonts[size]


def check_palette():
    for name, color in vars(sprites).items():
        colors = color if name == "PLAYER_COLORS" else [color]
        if name.isupper() and isinstance(color, (tuple, list)) and name != "SS":
            for red, green, blue in colors:
                assert max(red, green, blue) >= 90, f"{name} could read as a physical wall"
                assert not (red >= 160 and red - max(green, blue) >= 60), f"{name} could read as a laser dot"


def rotated(image, degrees):
    return pygame.transform.rotozoom(image, -degrees, 1)


def put(surface, image, center):
    surface.blit(image, image.get_rect(center=center))


def crewed(image, head, size):
    surface = pygame.Surface(size, pygame.SRCALPHA)
    put(surface, image, (size[0] // 2, size[1] // 2))
    put(surface, head, (size[0] // 2, size[1] // 2))
    return surface


def sheet(path):
    zoom = 3
    blue, yellow, purple = sprites.PLAYER_COLORS
    head = sprites.tim(18 * 1.05, zoom * .82)
    jouster = pygame.Surface((330, 190), pygame.SRCALPHA)
    put(jouster, sprites.horn(54, zoom * .8), (245, 95))
    put(jouster, sprites.canoe(18, purple, zoom * .8), (120, 95))
    put(jouster, sprites.tim(18 * 1.05, zoom * .8), (120, 95))
    items = [
        ("Tim (always upright)", sprites.tim(18, zoom * 1.6)),
        ("Canoe + Tim (P1)", crewed(sprites.canoe(18, blue, zoom * .82), head, (240, 200))),
        ("Canoe + Tim (P2)", crewed(sprites.canoe(18, yellow, zoom * .82), head, (240, 200))),
        ("Canoe + Tim (P3)", crewed(sprites.canoe(18, purple, zoom * .82), head, (240, 200))),
        ("Ejected Tim (swim ring)", crewed(sprites.swimmer(10, blue, zoom * 1.3), sprites.tim(14.5, zoom * 1.3, paws=False), (240, 200))),
        ("Jouster power-up active", jouster),
        ("Lily pad (shoot for power-up)", sprites.lily_pad(16, zoom)),
        ("Drifting boulder", sprites.boulder(22, 1, zoom)),
        ("Thrown rock", sprites.pebble(5, 3, zoom * 3)),
        ("Log", sprites.log(116, 22, zoom * .62)),
        ("Stick (drawn line)", sprites.stick(8, 122, zoom * .55)),
        ("Pickup: laser", sprites.pickup(14, "laser", zoom * 1.3)),
        ("Pickup: jouster", sprites.pickup(14, "jouster", zoom * 1.3)),
        ("Pickup: mine", sprites.pickup(14, "mine", zoom * 1.3)),
        ("Pufferfish mine (armed)", sprites.pufferfish(9, True, zoom * 2.4)),
    ]
    columns, cell_w, cell_h, top = 5, 270, 262, 84
    rows = math.ceil(len(items) / columns)
    surface = pygame.Surface((columns * cell_w + 40, rows * cell_h + top + 24))
    surface.fill(sprites.WHITE)
    title = font(50).render("Beaver Battle  -  sprite preview", True, sprites.INK)
    pygame.draw.rect(surface, sprites.CREAM, title.get_rect(center=(surface.get_width() // 2, 44)).inflate(56, 24), border_radius=22)
    put(surface, title, (surface.get_width() // 2, 44))
    for index, (label, image) in enumerate(items):
        x = 20 + (index % columns) * cell_w + cell_w // 2
        y = top + (index // columns) * cell_h
        put(surface, image, (x, y + 112))
        text = font(25).render(label, True, sprites.INK)
        pygame.draw.rect(surface, sprites.CREAM, text.get_rect(center=(x, y + 236)).inflate(22, 12), border_radius=13)
        put(surface, text, (x, y + 236))
    pygame.image.save(surface, path)


if __name__ == "__main__":
    check_palette()
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    sheet(out / "sprite_sheet.png")
    print("ok")
