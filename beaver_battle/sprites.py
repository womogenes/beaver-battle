"""Cute pastel sprites drawn in code. Sprites face +x so the game can rotate them by heading.

Every colour stays bright and un-red: vision.py reads max(channel) < 75 as a physical wall
and red >= 160 with red - max(green, blue) >= 60 as a laser dot.
"""

import math
from pathlib import Path
import random

import pygame

SS = 4
WEIGHT = 1.6

INK = (88, 98, 170)
EYE = (70, 72, 125)
CREAM = (253, 247, 236)
BUTTER = (243, 218, 142)
BUTTER_DARK = (228, 192, 112)
PINK = (244, 186, 198)
PINK_DARK = (232, 162, 184)
PEACH = (250, 208, 165)
SKY = (107, 198, 220)
SKY_LIGHT = (170, 225, 240)
LAVENDER = (196, 178, 235)
LAVENDER_LIGHT = (222, 210, 245)
MINT = (160, 215, 185)
MINT_DARK = (104, 172, 148)
BARK = (178, 132, 100)
BARK_LIGHT = (206, 166, 130)
BARK_LINE = (120, 78, 62)
WOOD = (238, 208, 162)
WOOD_LIGHT = (248, 228, 190)
WOOD_DARK = (202, 162, 122)
FUR = (200, 150, 110)
FUR_LINE = (112, 64, 54)
FUR_DARK = (152, 98, 82)
BELLY = (250, 232, 196)
BLUSH = (240, 186, 192)
BEAVER_EYE = (96, 64, 66)
BROWN = (184, 140, 108)
BROWN_LIGHT = (240, 214, 178)
BROWN_DARK = (130, 98, 86)
STONE = (176, 182, 208)
STONE_LIGHT = (206, 211, 230)
STONE_PALE = (228, 231, 243)
STONE_DARK = (146, 153, 188)
WATER_TOP = (208, 239, 240)
WATER_BOTTOM = (180, 223, 240)
WHITE = (255, 255, 255)
BLUE = (44, 78, 178)  # Reading text: dark, saturated and un-red, so it survives a washed-out projector.
BLUE_BRIGHT = (78, 138, 228)

ASSETS = Path(__file__).resolve().parents[1] / "assets"
art_cache = {}

PLAYER_COLORS = [(122, 200, 232), (245, 208, 132), (198, 172, 236)]


def shade(color, amount):
    return tuple(max(0, min(255, round(value * amount))) for value in color[:3])


def art(name):
    """Reference artwork cut by checks/build_assets.py; loaded once."""
    if name not in art_cache:
        image = pygame.image.load(ASSETS / name)
        art_cache[name] = image.convert_alpha() if pygame.display.get_surface() else image
    return art_cache[name]


def typeface(size):
    """Lilita One, the chunky display face; pygame's default if the file is missing."""
    key = ("typeface", size)
    if key not in art_cache:
        if not pygame.font.get_init():
            pygame.font.init()
        path = ASSETS / "fonts" / "LilitaOne-Regular.ttf"
        art_cache[key] = pygame.font.Font(path if path.is_file() else None, size)
    return art_cache[key]


def lettering(message, size, color, spacing=0):
    """Plain text in the display face, surviving a pygame restart since the font was opened.

    spacing opens the letters up, which heavily outlined text needs or its outlines run together.
    """
    try:
        font = typeface(size)
        font.size("A")
    except pygame.error:
        del art_cache[("typeface", size)]
        font = typeface(size)
    if not spacing or len(message) < 2:
        return font.render(message, True, color)
    glyphs = [font.render(letter, True, color) for letter in message]
    image = pygame.Surface((sum(glyph.get_width() for glyph in glyphs) + spacing * (len(glyphs) - 1), font.get_height()), pygame.SRCALPHA)
    x = 0
    for glyph in glyphs:
        image.blit(glyph, (x, 0))
        x += glyph.get_width() + spacing
    return image


def fitted(image, width):
    """An image shrunk, if it must be, to fit a width: header text never runs into its neighbours."""
    if image.get_width() <= width:
        return image
    return pygame.transform.smoothscale(image, (max(1, round(width)), max(1, round(image.get_height() * width / image.get_width()))))


def time_bar(surface, area, left, clock=0.0, hurry=.2):
    """Time left as a draining bar, read at a glance from across the room where a small number is not. `left` runs
    from 1 to 0; in the last stretch the bar blinks between two blues (never red: the camera reads red as a laser)."""
    area = pygame.Rect(area)
    round_by = area.height // 2
    pygame.draw.rect(surface, BLUE, area.inflate(area.height // 2, area.height // 2), border_radius=round_by + area.height // 4)
    pygame.draw.rect(surface, WHITE, area, border_radius=round_by)
    left = max(0.0, min(1.0, left))
    if left > 0:
        color = BLUE_BRIGHT if left > hurry or int(clock * 5) % 2 else SKY
        fill = pygame.Rect(area.x, area.y, max(area.height, round(area.width * left)), area.height)
        pygame.draw.rect(surface, color, fill, border_radius=round_by)


def sign(message, size, color=BLUE):
    """Reading text for a projector: solid dark-blue letters inside a white halo.

    On a whiteboard dark-on-light is what survives room light; pale fills and thin outlines wash out.
    """
    key = ("sign", message, size, color)
    if key not in art_cache:
        if sum(1 for name in art_cache if name[0] == "sign") > 300:
            for name in [name for name in art_cache if name[0] == "sign"]:
                del art_cache[name]
        spacing, reach = round(size * .03), max(2, round(size * .09))
        face, glow = lettering(message, size, color, spacing), lettering(message, size, WHITE, spacing)
        image = pygame.Surface((face.get_width() + 2 * reach, face.get_height() + 2 * reach), pygame.SRCALPHA)
        for inner in range(reach, 0, -2):
            for step in range(20):
                image.blit(glow, (reach + inner * math.cos(step * math.tau / 20), reach + inner * math.sin(step * math.tau / 20)))
        image.blit(face, (reach, reach))
        art_cache[key] = image
    return art_cache[key]


def label(message, size, fill=WHITE, ink=INK, tilt=0):
    """Sticker lettering: glossy two-tone letters, a white rim, a fat outline and a solid block of shadow.

    The fill must be a pastel: black outlines would read as walls and red letters as laser dots.
    """
    key = ("label", message, size, fill, ink, tilt)
    if key not in art_cache:
        if sum(1 for name in art_cache if name[0] == "label") > 300:
            for name in [name for name in art_cache if name[0] == "label"]:
                del art_cache[name]
        big, spacing = size >= 30, 0
        if fill == WHITE:
            # White letters have no colour of their own to stand on, so they get the most line work:
            # a heavy outline, a white halo beyond it, and a fine outer line to close the halo off.
            heavy, halo, fine = max(2, round(size * .12)), max(1, round(size * .06)), max(1, round(size * .035))
            rings = [(ink, heavy + halo + fine), (WHITE, heavy + halo), (ink, heavy)] if size >= 24 else [(ink, heavy + 1)]
            spacing = round(size * .07)
        else:
            rim = max(2, round(size * .045)) if big else 0
            rings = [(ink, rim + max(2, round(size * .085)))] + ([(WHITE, rim)] if rim else [])
        edge, drop = rings[0][1], max(2, round(size * (.16 if big else .12)))
        face, shine = lettering(message, size, fill, spacing), lettering(message, size, tint(fill, .5), spacing)

        def ringed(color, reach):
            source = lettering(message, size, color, spacing)
            ring = pygame.Surface((face.get_width() + 2 * edge, face.get_height() + 2 * edge), pygame.SRCALPHA)
            steps = max(28, round(reach * 6))
            for step in range(steps):
                ring.blit(source, (edge + reach * math.cos(step * math.tau / steps), edge + reach * math.sin(step * math.tau / steps)))
            if reach > 3:
                # Fill the band between the letter and its outermost echo, so thick outlines stay solid.
                for inner in range(3, round(reach), 3):
                    for step in range(steps):
                        ring.blit(source, (edge + inner * math.cos(step * math.tau / steps), edge + inner * math.sin(step * math.tau / steps)))
            return ring

        outline = ringed(*rings[0])
        image = pygame.Surface((outline.get_width(), outline.get_height() + drop), pygame.SRCALPHA)
        for fall in range(drop + 1):
            image.blit(outline, (0, fall))
        for color, reach in rings[1:]:
            image.blit(ringed(color, reach), (0, 0))
        image.blit(face, (edge, edge))
        if big and fill != WHITE:
            # A lighter upper half, cut on a gentle slant, reads as gloss.
            gloss = shine.copy()
            cut = pygame.Surface(gloss.get_size(), pygame.SRCALPHA)
            height = gloss.get_height()
            pygame.draw.polygon(cut, (255, 255, 255, 255), [(0, 0), (gloss.get_width(), 0), (gloss.get_width(), height * .46), (0, height * .58)])
            gloss.blit(cut, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            image.blit(gloss, (edge, edge))
        art_cache[key] = pygame.transform.rotozoom(image, tilt, 1) if tilt else image
    return art_cache[key]


def fitted(image, long_side, degrees=0, flip=False):
    if flip:
        image = pygame.transform.flip(image, True, False)
    scale = long_side / max(image.get_size())
    image = pygame.transform.smoothscale(image, (max(1, round(image.get_width() * scale)), max(1, round(image.get_height() * scale))))
    return pygame.transform.rotozoom(image, degrees, 1) if degrees else image


def tint(color, amount):
    return tuple(round(value + (255 - value) * amount) for value in color[:3])


class Pen:
    """A supersampled canvas in design units, centred on the origin."""

    def __init__(self, width, height, unit, zoom=1):
        self.k = unit * SS
        self.line_scale = zoom * SS
        self.surface = pygame.Surface((math.ceil(width * self.k), math.ceil(height * self.k)), pygame.SRCALPHA)
        self.cx, self.cy = self.surface.get_width() / 2, self.surface.get_height() / 2

    def pt(self, point):
        return (self.cx + point[0] * self.k, self.cy + point[1] * self.k)

    def stroke(self, points, color=INK, width=2, closed=True):
        pixels = max(1, round(width * WEIGHT * self.line_scale))
        mapped = [self.pt(point) for point in points]
        pairs = zip(mapped, mapped[1:] + mapped[:1]) if closed else zip(mapped, mapped[1:])
        for start, end in pairs:
            along = pygame.Vector2(end) - start
            if along.length_squared() == 0:
                continue
            across = along.normalize().rotate(90) * pixels / 2
            pygame.draw.polygon(self.surface, color, [start + across, end + across, end - across, start - across])
        for point in mapped:
            pygame.draw.circle(self.surface, color, point, pixels / 2)

    def poly(self, color, points, outline=INK, width=2):
        pygame.draw.polygon(self.surface, color, [self.pt(point) for point in points])
        if outline:
            self.stroke(points, outline, width)

    def ellipse(self, color, center, rx, ry=None, angle=0, outline=INK, width=2):
        ry = rx if ry is None else ry
        cos, sin = math.cos(angle), math.sin(angle)
        points = []
        for index in range(48):
            x, y = rx * math.cos(index * math.tau / 48), ry * math.sin(index * math.tau / 48)
            points.append((center[0] + x * cos - y * sin, center[1] + x * sin + y * cos))
        self.poly(color, points, outline, width)

    def oval(self, center, rx, ry=None, angle=0):
        ry = rx if ry is None else ry
        cos, sin = math.cos(angle), math.sin(angle)
        curve = [(rx * math.cos(index * math.tau / 48), ry * math.sin(index * math.tau / 48)) for index in range(48)]
        return [(center[0] + x * cos - y * sin, center[1] + x * sin + y * cos) for x, y in curve]

    def union(self, shapes, width=2, ink=INK):
        """Fill overlapping shapes as one flat silhouette with a single outer outline."""
        for color, points in shapes:
            self.stroke(points, ink, width * 2)
        for color, points in shapes:
            self.poly(color, points, None)

    def line(self, color, start, end, width=2):
        self.stroke([start, end], color, width, closed=False)

    def image(self):
        size = (max(1, round(self.surface.get_width() / SS)), max(1, round(self.surface.get_height() / SS)))
        return pygame.transform.smoothscale(self.surface, size)


def hull_points(half_length, half_width, steps=28):
    top = []
    for index in range(steps + 1):
        x = -half_length + 2 * half_length * index / steps
        top.append((x, -half_width * max(0, 1 - (x / half_length) ** 2) ** .62))
    return top + [(x, -y) for x, y in reversed(top[1:-1])]


def tail_shapes(pen, x, size=1):
    return [(FUR_DARK, pen.oval((x, 0), 13 * size, 9 * size))]


def tail_hatch(pen, x, size=1):
    for offset in (-7, -1, 5):
        pen.line(FUR_LINE, (x + (offset - 3.5) * size, -5.5 * size), (x + (offset + 3.5) * size, 5.5 * size), .9)
        pen.line(FUR_LINE, (x + (offset + 3.5) * size, -5.5 * size), (x + (offset - 3.5) * size, 5.5 * size), .9)


def tim(radius, zoom=1, paws=True):
    """Tim the beaver, kawaii and always upright: big glossy eyes, blush, buck teeth, hair tuft."""
    pen = Pen(48, 46, radius * zoom / 17, zoom)
    shapes = [(FUR, pen.oval((side * 12.5, -11), 5.6)) for side in (-1, 1)]
    shapes.append((FUR, [(-5, -13), (-3.5, -19.5), (-.5, -15.5), (2, -20.5), (4, -15.5), (7.5, -18), (7, -12)]))
    shapes += [(FUR, pen.oval((side * 12, 4.5), 7, 6.5)) for side in (-1, 1)]
    shapes.append((FUR, pen.oval((0, 0), 17, 14.5)))
    if paws:
        shapes += [(FUR, pen.oval((side * 8, 15), 4, 3.4)) for side in (-1, 1)]
    pen.union(shapes, 1.5, FUR_LINE)
    for side in (-1, 1):
        pen.ellipse(FUR_DARK, (side * 12.5, -11), 2.8, outline=None)
    pen.ellipse(FUR, (0, -4), 13, 9, outline=None)
    for side in (-1, 1):
        pen.ellipse(BLUSH, (side * 12.5, 6), 3.6, 2.5, outline=None)
        pen.ellipse(BEAVER_EYE, (side * 7.6, 1), 4.3, 4.6, outline=None)
        pen.ellipse(WHITE, (side * 7.6 - 1.5, -.8), 1.7, outline=None)
        pen.ellipse(WHITE, (side * 7.6 + 1.6, 2.8), .9, outline=None)
    pen.poly(WHITE, [(-2.8, 6.2), (2.8, 6.2), (2.8, 11), (1.6, 12), (-1.6, 12), (-2.8, 11)], FUR_LINE, .9)
    pen.line(FUR_LINE, (0, 7), (0, 12), .7)
    pen.stroke([(-4.6, 5.2), (-3.4, 6.6), (-1.2, 6.6), (0, 5.2), (1.2, 6.6), (3.4, 6.6), (4.6, 5.2)], FUR_LINE, .9, closed=False)
    pen.poly(BEAVER_EYE, [(-2, 3), (2, 3), (0, 5.4)], FUR_LINE, .8)
    if paws:
        for side in (-1, 1):
            pen.line(FUR_LINE, (side * 8, 14), (side * 8, 17), .7)
    return pen.image()


def canoe(radius, color, zoom=1):
    """The hull, paddle and Tim's tail; the game stamps an upright tim() on top."""
    pen = Pen(132, 112, 2.3 * radius * zoom / 60, zoom)
    pen.line(WOOD_DARK, (4, 12), (19, 43), 2.4)
    pen.ellipse(tint(color, .25), (21.5, 48), 9.5, 5.4, math.radians(65))
    pen.poly(color, hull_points(60, 30))
    pen.poly(WOOD_LIGHT, hull_points(49, 22), None)
    pen.line(WOOD_DARK, (40, -10), (40, 10), 2.6)
    pen.union(tail_shapes(pen, -33, 1.05), 1.5, FUR_LINE)
    tail_hatch(pen, -33, 1.05)
    return pen.image()


def ring_band(inner, outer, start, sweep):
    angles = [start + sweep * index / 8 for index in range(9)]
    return [(outer * math.cos(a), outer * math.sin(a)) for a in angles] + [(inner * math.cos(a), inner * math.sin(a)) for a in reversed(angles)]


def swimmer(radius, color, zoom=1):
    """The swim ring and tail; the game stamps an upright tim() on top."""
    pen = Pen(96, 76, 1.9 * radius * zoom / 26, zoom)
    pen.union(tail_shapes(pen, -31, .95), 1.5, FUR_LINE)
    tail_hatch(pen, -31, .95)
    pen.ellipse(color, (0, 0), 26)
    for index in range(4):
        pen.poly(CREAM, ring_band(0, 24.5, index * math.tau / 4 + .45, .62), None)
    pen.stroke(pen.oval((0, 0), 26))
    return pen.image()


def horn(length, zoom=1):
    pen = Pen(64, 20, length * zoom / 56, zoom)
    pen.poly(CREAM, [(-28, -7), (28, 0), (-28, 7)])
    for index in range(5):
        x = -25 + index * 9
        pen.line(PINK_DARK, (x, -7 * (28 - x) / 56), (x + 6, 7 * (22 - x) / 56), 2.4)
    pen.stroke([(-28, -7), (28, 0), (-28, 7)])
    return pen.image()


def blob(rng, radius, count, low=.84):
    points = []
    for index in range(count):
        angle = index * math.tau / count + rng.uniform(-.12, .12)
        distance = radius * rng.uniform(low, 1)
        points.append((distance * math.cos(angle), distance * math.sin(angle)))
    for repeat in range(3):
        rounded = []
        for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
            rounded += [(ax * .75 + bx * .25, ay * .75 + by * .25), (ax * .25 + bx * .75, ay * .25 + by * .75)]
        points = rounded
    return points


def boulder(radius, seed=1, zoom=1):
    rng = random.Random(seed)
    return fitted(art("rock.png"), radius * zoom * 2.1, rng.choice((0, 25, 160, 200, 335)), rng.random() < .5)


def pebble(radius, seed=3, zoom=1):
    return fitted(art("rock.png"), radius * zoom * 2.8, random.Random(seed).uniform(0, 360))


def droplet(radius, zoom=1):
    pen = Pen(44, 26, radius * zoom / 8, zoom)
    points = [(-19, 0)] + [(4 + 8.5 * math.cos(a), 8.5 * math.sin(a)) for a in [math.radians(d) for d in range(-125, 126, 10)]]
    pen.poly(SKY_LIGHT, points, INK, 1.5)
    pen.ellipse(WHITE, (7, -3), 2.6, 1.8, -.6, outline=None)
    return pen.image()


def barrel(radius, font, zoom=1):
    pen = Pen(58, 58, radius * zoom / 20, zoom)
    pen.ellipse(WOOD, (0, 0), 20.5)
    for index in range(12):
        angle = index * math.tau / 12
        pen.line(WOOD_DARK, (15 * math.cos(angle), 15 * math.sin(angle)), (19.5 * math.cos(angle), 19.5 * math.sin(angle)), .8)
    pen.ellipse(LAVENDER, (0, 0), 15.5, width=1)
    pen.ellipse(CREAM, (0, 0), 11.5, width=1)
    for index in range(4):
        angle = index * math.tau / 4 + math.pi / 4
        pen.ellipse(CREAM, (13.5 * math.cos(angle), 13.5 * math.sin(angle)), 1.1, outline=None)
    mark = font(round(22 * pen.k)).render("?", True, INK)
    pen.surface.blit(mark, mark.get_rect(center=(pen.cx, pen.cy + pen.k)))
    return pen.image()


def lily_pad(radius, zoom=1, degrees=0, flower=True):
    """A lily pad; shooting one releases a power-up, hinted at by the little flower."""
    pad = fitted(art("lily_pad.png"), radius * zoom * 2.15, degrees)
    if flower:
        pen = Pen(24, 24, radius * zoom / 21, zoom)
        pen.union([(PINK, pen.oval((6.5 * math.cos(a), 6.5 * math.sin(a)), 5, 3.4, a)) for a in [index * math.tau / 6 for index in range(6)]], 1.2)
        pen.ellipse(BUTTER, (0, 0), 3.4, width=1.2)
        bloom = pen.image()
        pad = pad.copy()
        pad.blit(bloom, bloom.get_rect(center=(pad.get_width() * .44, pad.get_height() * .56)))
    return pad


def squirt_plant(radius, zoom=1):
    """The rotating head of a turret: a pink bulb that squirts water from its spout."""
    pen = Pen(72, 50, radius * zoom / 21, zoom)
    for degrees in (128, 180, 232):
        angle = math.radians(degrees)
        pen.ellipse(tint(MINT, .1), (13 * math.cos(angle), 13 * math.sin(angle)), 9, 4.6, angle, MINT_DARK, 1.5)
    pen.union([(PINK, [(6, -4.2), (21, -4.2), (21, 4.2), (6, 4.2)]), (PINK, pen.oval((22, 0), 3.4, 6.6)), (PINK, pen.oval((0, 0), 13))], 1.6)
    pen.ellipse(SKY, (23.4, 0), 1.3, 3.4, outline=None)
    for side in (-1, 1):
        pen.ellipse(EYE, (4, side * 5.2), 2.5, outline=None)
        pen.ellipse(WHITE, (4.9, side * 5.2 - .9), .9, outline=None)
        pen.ellipse(PINK_DARK, (8, side * 9), 2.3, outline=None)
    return pen.image()


def beam_lotus(radius, zoom=1):
    pen = Pen(76, 66, radius * zoom / 18, zoom)
    for ring, (distance, rx, ry, color) in enumerate(((15, 10.5, 5.6, LAVENDER), (9.5, 8, 4.6, LAVENDER_LIGHT))):
        petals = [(color, pen.oval((distance * math.cos(a), distance * math.sin(a)), rx, ry, a))
                  for a in [index * math.tau / 8 + ring * math.tau / 16 for index in range(8)]]
        pen.union(petals, 1.3)
    pen.union([(BUTTER, [(3, -5), (21, 0), (3, 5)]), (BUTTER, pen.oval((0, 0), 7.2))], 1.3)
    for side in (-1, 1):
        pen.ellipse(EYE, (1.8, side * 2.9), 1.5, outline=None)
    return pen.image()


def rounded_block(width, height, zoom):
    size = (max(1, round(width * zoom * SS)), max(1, round(height * zoom * SS)))
    return pygame.Surface(size, pygame.SRCALPHA), round(min(size) * .28), max(1, round(2 * WEIGHT * zoom * SS))


def finish_block(surface, mask_radius, line):
    mask = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(mask, WHITE, mask.get_rect(), border_radius=mask_radius)
    surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return pygame.transform.smoothscale(surface, (max(1, surface.get_width() // SS), max(1, surface.get_height() // SS)))


def log(width, height, zoom=1, seed=8):
    """A floating log: bark wallpaper fill, a thin bark-brown edge, and end grain on both cut faces."""
    rng = random.Random(seed)
    across = height > width
    long, short = (max(width, height) * zoom, min(width, height) * zoom)
    surface = pygame.Surface((round(long * SS), round(short * SS)), pygame.SRCALPHA)
    side = round(110 * zoom * SS)
    texture = pygame.transform.smoothscale(pygame.transform.rotate(art("bark.png"), 90), (side, side))
    for x in range(-rng.randrange(side // 2), surface.get_width(), side):
        for y in range(-rng.randrange(side // 2), surface.get_height(), side):
            surface.blit(texture, (x, y))
    cap, line = short * SS * .34, max(1, round(1.6 * zoom * SS))
    for x in (cap, surface.get_width() - cap):
        face = pygame.Rect(0, 0, cap * 2, surface.get_height())
        face.center = (x, surface.get_height() / 2)
        pygame.draw.ellipse(surface, WOOD_LIGHT, face)
        pygame.draw.ellipse(surface, BARK_LINE, face, line)
        pygame.draw.ellipse(surface, WOOD_DARK, face.inflate(-cap * .9, -surface.get_height() * .45), line)
    mask = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(mask, WHITE, mask.get_rect(), border_radius=round(short * SS * .5))
    surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pygame.draw.rect(surface, BARK_LINE, surface.get_rect(), line, border_radius=round(short * SS * .5))
    image = pygame.transform.smoothscale(surface, (max(1, round(long)), max(1, round(short))))
    return pygame.transform.rotate(image, 90) if across else image


def stick(width, height, zoom=1, seed=2):
    """A thin, slightly crooked twig with tapered ends and little side shoots."""
    rng = random.Random(seed)
    across = height > width
    long, short = max(width, height), min(width, height)
    pen = Pen(long + 36, short + 36, zoom, zoom)
    steps = max(4, round(long / 22))
    spine = [(-long / 2 + long * index / steps, rng.uniform(-.28, .28) * short if 0 < index < steps else 0) for index in range(steps + 1)]
    upper, lower = [], []
    for index, (x, y) in enumerate(spine):
        half = short / 2 * (.5 + .5 * math.sin(math.pi * (.12 + .76 * index / steps)))
        upper.append((x, y - half))
        lower.append((x, y + half))
    shapes = []
    for shoot in range(max(2, round(long / 45))):
        x, y = spine[rng.randrange(1, steps)]
        angle = rng.choice((-1, 1)) * math.radians(rng.uniform(35, 60)) + (math.pi if rng.random() < .3 else 0)
        reach, thick = rng.uniform(9, 16), short * .22
        tip = (x + reach * math.cos(angle), y + reach * math.sin(angle))
        normal = (-math.sin(angle) * thick, math.cos(angle) * thick)
        shapes.append((BARK, [(x + normal[0], y + normal[1]), tip, (x - normal[0], y - normal[1])]))
    pen.union(shapes + [(BARK, upper + lower[::-1])], 1.1, BARK_LINE)
    for index in range(1, steps):
        x, y = spine[index]
        pen.line(BARK_LIGHT, (x - 4, y - short * .12), (x + 4, y - short * .12), .8)
    image = pen.image()
    return pygame.transform.rotate(image, 90) if across else image


def pufferfish(radius, armed=True, zoom=1):
    pen = Pen(38, 38, radius * zoom / 10, zoom)
    if armed:
        pen.ellipse((250, 236, 170, 150), (0, 0), 16, outline=None)
    spikes = []
    for index in range(10):
        angle = index * math.tau / 10 + .3
        left, right = angle - .3, angle + .3
        spikes.append((LAVENDER_LIGHT, [(15 * math.cos(angle), 15 * math.sin(angle)), (9 * math.cos(left), 9 * math.sin(left)),
                                        (9 * math.cos(right), 9 * math.sin(right))]))
    pen.union(spikes + [(LAVENDER, pen.oval((0, 0), 10))], 1.1)
    pen.ellipse(CREAM, (-3.5, 0), 4.5, 6, outline=None)
    for side in (-1, 1):
        pen.ellipse(EYE, (3.4, side * 3.9), 1.9, outline=None)
        pen.ellipse(WHITE, (4, side * 3.9 - .7), .7, outline=None)
        pen.ellipse(PINK_DARK, (5.4, side * 6.6), 1.5, outline=None)
    pen.ellipse(PINK, (7.6, 0), 1.5, 1.9, width=1)
    return pen.image()


def pickup(radius, kind, zoom=1):
    pen = Pen(48, 48, radius * zoom / 14, zoom)
    pad = fitted(art("lily_pad.png"), pen.surface.get_width() * .96, 200)
    pen.surface.blit(pad, pad.get_rect(center=(pen.cx, pen.cy)))
    if kind == "laser":
        pen.poly(SKY, [(3, -14), (-8, 2), (-1, 2), (-4, 14), (9, -3), (1.5, -3)], INK, 1.5)
    elif kind == "jouster":
        tip, left, right = (13, -13), (-12, 4), (-4, 12)
        pen.poly(CREAM, [left, tip, right], None)
        for step in (.12, .38, .62):
            start = (left[0] + (tip[0] - left[0]) * step, left[1] + (tip[1] - left[1]) * step)
            end = (right[0] + (tip[0] - right[0]) * (step + .16), right[1] + (tip[1] - right[1]) * (step + .16))
            pen.line(PINK_DARK, start, end, 2.2)
        pen.stroke([left, tip, right], INK, 1.5)
    else:
        small = pufferfish(radius * .58, False, zoom)
        big = pygame.transform.smoothscale(small, (small.get_width() * SS, small.get_height() * SS))
        pen.surface.blit(big, big.get_rect(center=(pen.cx, pen.cy)))
    return pen.image()



def tiled(name, side, turn=0):
    """A wallpaper texture at a given tile size, cached."""
    key = (name, side, turn)
    if key not in art_cache:
        texture = pygame.transform.rotate(art(name), turn) if turn else art(name)
        art_cache[key] = pygame.transform.smoothscale(texture, (side, side))
    return art_cache[key]


def outline_fill(kind, mask, angle, edge, zoom=1):
    """Fill a closed marker outline and trace its edge: bark with the grain along a log, the rock art for a rock.

    mask is the shape's uint8 alpha inside its padded bounding box; edge is its outline in the same coordinates.
    """
    height, width = mask.shape
    image = pygame.Surface((width, height), pygame.SRCALPHA)
    if kind == "log":
        tile = tiled("bark.png", max(8, round(120 * zoom)), 90)
        reach = math.ceil(math.hypot(width, height)) + 2
        grain = pygame.Surface((reach, reach), pygame.SRCALPHA)
        for x in range(0, reach, tile.get_width()):
            for y in range(0, reach, tile.get_height()):
                grain.blit(tile, (x, y))
        grain = pygame.transform.rotozoom(grain, -math.degrees(angle), 1)
        image.blit(grain, grain.get_rect(center=(width / 2, height / 2)))
    else:
        # The single rock, enlarged so its facets span the outline and its own rim falls outside it.
        image.fill(STONE)
        rock = art("rock.png")
        scale = 1.22 * max(width / rock.get_width(), height / rock.get_height())
        rock = pygame.transform.smoothscale(rock, (round(rock.get_width() * scale), round(rock.get_height() * scale)))
        image.blit(rock, rock.get_rect(center=(width / 2, height / 2)))
    alpha = pygame.surfarray.pixels_alpha(image)
    alpha[:] = mask.T
    del alpha
    color = BARK_LINE if kind == "log" else STONE_DARK
    trace = pygame.Surface((width * SS, height * SS), pygame.SRCALPHA)
    points = [(x * SS, y * SS) for x, y in edge]
    line = max(2, round(6 * zoom * SS))
    pygame.draw.lines(trace, color, True, points, line)
    for point in points:
        pygame.draw.circle(trace, color, point, line / 2)
    image.blit(pygame.transform.smoothscale(trace, (width, height)), (0, 0))
    return image


def water(width, height, cell=.62, lighten=.8):
    """The water wallpaper as a faint backdrop: mirror-tiled so it has no seams, then washed most of
    the way to white so the board stays bright for the camera and the sprites stay the loudest thing."""
    texture = art("water.jpg")
    scale = max(width / texture.get_width(), height / texture.get_height()) * cell
    tile = pygame.transform.smoothscale(texture, (math.ceil(texture.get_width() * scale), math.ceil(texture.get_height() * scale)))
    surface = pygame.Surface((width, height), 0, 24)
    for column in range(math.ceil(width / tile.get_width())):
        for row in range(math.ceil(height / tile.get_height())):
            surface.blit(pygame.transform.flip(tile, column % 2 == 1, row % 2 == 1), (column * tile.get_width(), row * tile.get_height()))
    wash = pygame.Surface((width, height), pygame.SRCALPHA)
    wash.fill((255, 255, 255, round(255 * lighten)))
    surface.blit(wash, (0, 0))
    return surface


def tiled_ground(name, width, height, tile_width, lighten=0.0):
    """A wallpaper mirror-tiled across the board, optionally washed toward white."""
    texture = art(name)
    scale = tile_width / texture.get_width()
    tile = pygame.transform.smoothscale(texture, (math.ceil(texture.get_width() * scale), math.ceil(texture.get_height() * scale)))
    surface = pygame.Surface((width, height), 0, 24)
    for column in range(math.ceil(width / tile.get_width())):
        for row in range(math.ceil(height / tile.get_height())):
            surface.blit(pygame.transform.flip(tile, column % 2 == 1, row % 2 == 1), (column * tile.get_width(), row * tile.get_height()))
    if lighten:
        wash = pygame.Surface((width, height), pygame.SRCALPHA)
        wash.fill((255, 255, 255, round(255 * lighten)))
        surface.blit(wash, (0, 0))
    return surface


CHEST_WOOD = (158, 110, 74)
CHEST_DARK = (120, 80, 58)
GOLD = (245, 206, 96)


def chest(size, zoom=1, open_lid=False):
    """The treasure chest: banded wood, a gold lock, and coins when it is open."""
    pen = Pen(64, 60, size * zoom / 44, zoom)
    if open_lid:
        pen.poly(CHEST_DARK, [(-20, -8), (-17, -25), (17, -25), (20, -8)])
        for x, y, r in ((-9, -9, 5), (0, -12, 6), (9, -9, 5), (-4, -5, 5), (5, -5, 5)):
            pen.ellipse(GOLD, (x, y), r, width=1.4)
        for x, y in ((-14, -20), (13, -22), (1, -27)):
            pen.poly(WHITE, [(x, y - 4), (x + 1.2, y - 1.2), (x + 4, y), (x + 1.2, y + 1.2), (x, y + 4), (x - 1.2, y + 1.2), (x - 4, y), (x - 1.2, y - 1.2)], INK, 1)
    else:
        pen.poly(CHEST_WOOD, [(-21, -4), (-19, -16), (-11, -23), (11, -23), (19, -16), (21, -4)])
        pen.line(CHEST_DARK, (-12, -22), (-12, -5), 1.4)
        pen.line(CHEST_DARK, (12, -22), (12, -5), 1.4)
    pen.poly(CHEST_WOOD, [(-21, -4), (21, -4), (19, 20), (-19, 20)])
    for x in (-12, 12):
        pen.poly(GOLD, [(x - 3, -4), (x + 3, -4), (x + 3, 20), (x - 3, 20)], INK, 1.4)
    pen.line(CHEST_DARK, (-20, 8), (20, 8), 1.4)
    pen.ellipse(GOLD, (0, 2), 5.5, 6.5, width=1.6)
    pen.ellipse(CHEST_DARK, (0, 2.5), 1.6, 2.4, outline=None)
    pen.stroke([(-21, -4), (21, -4)], INK, 2, closed=False)
    return pen.image()


LEAF = (104, 176, 110)
LEAF_LIGHT = (146, 204, 132)
LEAF_DARK = (74, 140, 96)


def tree(radius, seed=1, zoom=1):
    """A tree from above: a lumpy canopy with a few lighter tufts."""
    rng = random.Random(seed)
    pen = Pen(72, 72, radius * zoom / 30, zoom)
    lumps = [(0, 0, 22)] + [(19 * math.cos(a), 19 * math.sin(a), rng.uniform(11, 15)) for a in [index * math.tau / 7 + rng.uniform(-.2, .2) for index in range(7)]]
    pen.union([(LEAF, pen.oval((x, y), r)) for x, y, r in lumps], 1.5, LEAF_DARK)
    for x, y, r in ((-8, -9, 9), (9, 4, 7), (-4, 12, 6)):
        pen.ellipse(LEAF_LIGHT, (x + rng.uniform(-2, 2), y + rng.uniform(-2, 2)), r, outline=None)
    for x, y in ((-10, -11), (8, 2), (-3, 11), (12, -10)):
        pen.ellipse(tint(LEAF_LIGHT, .45), (x, y), 2.4, outline=None)
    return pen.image()


FAWN = (214, 164, 112)
FAWN_DARK = (150, 100, 78)
FAWN_PALE = (250, 234, 204)
ANTLER = (146, 98, 76)


def deer(size, zoom=1):
    """A fawn sitting up and facing you, in the manner of Tim: big head, big eyes, pink ears, little antlers, spots."""
    pen = Pen(64, 70, size * zoom / 24, zoom)
    for side in (-1, 1):
        pen.stroke([(side * 6, -17), (side * 9, -29)], ANTLER, 3.4, closed=False)
        pen.stroke([(side * 8, -24), (side * 14, -28)], ANTLER, 3, closed=False)
        pen.stroke([(side * 8.5, -26), (side * 4, -31)], ANTLER, 3, closed=False)
    body = [(FAWN, pen.oval((0, 20), 13, 12)), (FAWN_PALE, pen.oval((15, 22), 5.5, 7, -.5))]
    body += [(FAWN_DARK, pen.oval((side * 8, 30), 5, 3.2)) for side in (-1, 1)]
    pen.union(body, 1.5, FUR_LINE)
    pen.ellipse(FAWN_PALE, (0, 19), 7, 9, outline=None)
    for x, y in ((9, 15), (11, 21), (-10, 17)):
        pen.ellipse(FAWN_PALE, (x, y), 1.7, outline=None)
    head = [(FAWN, pen.oval((side * 17, -9), 9, 5.5, side * -.75)) for side in (-1, 1)]
    head += [(FAWN, pen.oval((side * 11, 5), 7, 6.5)) for side in (-1, 1)] + [(FAWN, pen.oval((0, -2), 18, 15.5))]
    pen.union(head, 1.5, FUR_LINE)
    for side in (-1, 1):
        pen.ellipse(BLUSH, (side * 17, -9), 6, 3.2, side * -.75, outline=None)
    pen.ellipse(FAWN_PALE, (0, 5), 11.5, 8.5, outline=None)
    for x, y in ((-5, -13), (0, -15), (5, -13), (-9, -10)):
        pen.ellipse(FAWN_PALE, (x, y), 1.6, outline=None)
    for side in (-1, 1):
        pen.ellipse(BEAVER_EYE, (side * 8, -1), 4.4, 5, outline=None)
        pen.ellipse(WHITE, (side * 8 - 1.5, -3), 1.7, outline=None)
        pen.ellipse(WHITE, (side * 8 + 1.6, 1.4), .9, outline=None)
        pen.ellipse(BLUSH, (side * 13, 6), 3.2, 2.2, outline=None)
    pen.ellipse(BEAVER_EYE, (0, 4), 2.6, 2, outline=None)
    pen.stroke([(-4, 8), (-2, 10), (0, 8.4), (2, 10), (4, 8)], FUR_LINE, .9, closed=False)
    return pen.image()


def timber(length, width, zoom=1, seed=0):
    """A plain floating log: the bark wallpaper with its grain along the log, round ends and a bark-brown edge."""
    rng = random.Random(seed)
    size = (max(2, round(length * zoom * SS)), max(2, round(width * zoom * SS)))
    surface = pygame.Surface(size, pygame.SRCALPHA)
    side = max(8, round(width * zoom * SS * 2.6))
    texture = pygame.transform.smoothscale(pygame.transform.rotate(art("bark_rich.jpg"), 90), (side, side))
    for x in range(-rng.randrange(side // 2), size[0], side):
        for y in range(-rng.randrange(side // 2), size[1], side):
            surface.blit(texture, (x, y))
    shade = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(shade, (70, 40, 30, 70), (0, size[1] * .68, size[0], size[1] * .32))
    pygame.draw.rect(shade, (255, 240, 210, 46), (0, size[1] * .08, size[0], size[1] * .2))
    surface.blit(shade, (0, 0))
    corner = round(size[1] * .46)
    mask = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(mask, WHITE, mask.get_rect(), border_radius=corner)
    surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    pygame.draw.rect(surface, BARK_LINE, surface.get_rect(), max(1, round(2.4 * zoom * SS)), border_radius=corner)
    return pygame.transform.smoothscale(surface, (max(1, size[0] // SS), max(1, size[1] // SS)))


BERRY = {"red": ((226, 72, 86), (160, 40, 58)), "blue": ((132, 112, 214), (96, 84, 176))}


def berries(size, zoom=1, color="red"):
    """A sprig of berries: eat them for a burst of speed.

    Red is what was asked for and reads best on grass, but the camera takes saturated red for a laser dot
    (vision.py: red >= 160 and red - max(green, blue) >= 60). If berries confuse laser tracking on the real
    board, set [treasure] berry_color = "blue".
    """
    fill, edge = BERRY[color]
    pen = Pen(44, 44, size * zoom / 14, zoom)
    pen.stroke([(-2, -15), (0, -8), (4, -14)], LEAF_DARK, 1.6, closed=False)
    pen.ellipse(LEAF, (8, -13), 7, 3.6, -.5, LEAF_DARK, 1.2)
    pen.ellipse(LEAF, (-8, -13), 6, 3.2, .6, LEAF_DARK, 1.2)
    for x, y, r in ((-7, 2, 8), (7, 1, 8.5), (0, 10, 8)):
        pen.ellipse(fill, (x, y), r, outline=edge, width=1.4)
        pen.ellipse(tint(fill, .6), (x - r * .35, y - r * .35), r * .3, outline=None)
    return pen.image()


def lodge(size, zoom=1):
    """A beaver lodge: a dome of piled sticks with a doorway, and someone at home."""
    rng = random.Random(4)
    pen = Pen(80, 72, size * zoom / 34, zoom)
    pen.ellipse(BARK, (0, 6), 34, 26, outline=BARK_LINE, width=2)
    for index in range(26):
        x, y = rng.uniform(-28, 28), rng.uniform(-14, 26)
        if (x / 32) ** 2 + ((y - 6) / 24) ** 2 < .9:
            angle = rng.uniform(-.6, .6) + (math.pi / 2 if index % 5 == 0 else 0)
            reach = rng.uniform(6, 11)
            pen.line(BARK_LINE, (x - reach * math.cos(angle), y - reach * math.sin(angle)), (x + reach * math.cos(angle), y + reach * math.sin(angle)), 2.6)
            pen.line(BARK_LIGHT, (x - reach * math.cos(angle), y - reach * math.sin(angle) - .6), (x + reach * math.cos(angle), y + reach * math.sin(angle) - .6), 1.2)
    pen.ellipse(CHEST_DARK, (0, 20), 10, 9, outline=BARK_LINE, width=1.6)
    pen.ellipse(FUR, (0, 21), 6.5, 5.5, outline=None)
    for side in (-1, 1):
        pen.ellipse(BEAVER_EYE, (side * 2.6, 20), 1.3, outline=None)
    pen.poly(WHITE, [(-1.4, 23), (1.4, 23), (1.4, 25.6), (-1.4, 25.6)], FUR_LINE, .7)
    return pen.image()
