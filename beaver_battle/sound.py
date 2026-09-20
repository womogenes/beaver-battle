"""Sound effects synthesised with numpy at start-up, so the game ships no audio files.

game.py only names what happened (game.sounds); this module owns how each name sounds.
"""

import numpy as np

RATE = 44100


def span(seconds):
    return np.linspace(0, seconds, int(RATE * seconds), endpoint=False)


def sweep(seconds, start, end, shape="sine"):
    """A tone gliding from start to end Hz."""
    t = span(seconds)
    phase = 2 * np.pi * np.cumsum(start * (end / start) ** (t / seconds)) / RATE
    if shape == "square":
        return np.tanh(4 * np.sin(phase))
    if shape == "saw":
        return 2 * ((phase / (2 * np.pi)) % 1) - 1
    return np.sin(phase)


def decay(seconds, rate, attack=.004):
    t = span(seconds)
    return np.minimum(1, t / attack) * np.exp(-rate * t)


def noise(seconds, smooth, seed=7):
    """White noise softened by a moving average; a larger smooth sounds duller and wetter."""
    raw = np.random.default_rng(seed).uniform(-1, 1, int(RATE * seconds) + smooth)
    soft = np.convolve(raw, np.ones(smooth) / smooth, mode="valid")[:int(RATE * seconds)]
    return soft / max(1e-9, np.abs(soft).max())


def mix(*parts):
    out = np.zeros(max(len(part) for part in parts))
    for part in parts:
        out[:len(part)] += part
    return out


def after(seconds, part):
    return np.concatenate([np.zeros(int(RATE * seconds)), part])


def notes(pitches, length, shape="sine", rate=9):
    return mix(*[after(index * length * .8, sweep(length, pitch, pitch * 1.01, shape) * decay(length, rate))
                 for index, pitch in enumerate(pitches)])


def splash():
    """Someone sinks: a slap on the water, a wash of spray, then bubbles coming up."""
    rng = np.random.default_rng(3)
    slap = noise(.09, 3) * decay(.09, 38) * 1.1
    thump = sweep(.22, 150, 45) * decay(.22, 14) * .9
    spray = noise(.9, 10, 8) * decay(.9, 4.5, .03) * .8 + noise(.9, 60, 9) * decay(.9, 3, .06) * .7
    bubbles = [after(.22 + index * .075 + rng.uniform(0, .03), sweep(.07, pitch, pitch * 1.9) * decay(.07, 30) * .32)
               for index, pitch in enumerate(rng.uniform(320, 760, 8))]
    return mix(slap, thump, spray, *bubbles)


def build():
    """Every effect as a float array in [-1, 1], keyed by the names game.py emits."""
    return {
        "shoot": mix(sweep(.13, 980, 260) * decay(.13, 24) * .8, noise(.05, 2) * decay(.05, 70) * .5),
        "hit": mix(sweep(.28, 130, 38) * decay(.28, 9) * 1.2, noise(.07, 3, 11) * decay(.07, 32) * 1.0,
                   sweep(.16, 520, 120, "square") * decay(.16, 15) * .6, noise(.2, 30, 12) * decay(.2, 12) * .6,
                   after(.07, sweep(.2, 300, 560) * decay(.2, 13) * .35)),
        "splash": splash(),
        "thud": mix(sweep(.11, 190, 80) * decay(.11, 26), noise(.04, 12) * decay(.04, 60) * .5),
        "crack": mix(noise(.22, 5, 4) * decay(.22, 16), sweep(.16, 240, 70) * decay(.16, 18) * .7),
        "pickup": mix(sweep(.05, 300, 1500) * decay(.05, 30), after(.03, notes((784, 1047, 1568), .1) * .7)),
        "charm": sweep(.2, 500, 1100) * decay(.2, 9, .03) * .35,
        "whoosh": noise(.5, 24, 14) * np.sin(np.pi * span(.5) / .5) ** 2 * .6,
        "powerup": sweep(.32, 330, 1320, "square") * decay(.32, 6, .02) * .45,
        "laser": mix(sweep(.3, 2100, 240, "saw") * decay(.3, 9) * .4, sweep(.3, 1050, 120) * decay(.3, 9) * .5),
        "ram": mix(sweep(.16, 260, 70, "square") * decay(.16, 16) * .7, noise(.12, 6, 2) * decay(.12, 24) * .8),
        "reload": notes((440, 587), .07, rate=20) * .4,
        "drop": sweep(.14, 560, 140) * decay(.14, 20) * .8,
        "boom": mix(noise(.75, 40, 5) * decay(.75, 5.5), sweep(.6, 110, 32) * decay(.6, 6), noise(.08, 3) * decay(.08, 40) * .6),
        "bump": mix(sweep(.09, 150, 85) * decay(.09, 30) * .7, noise(.03, 20) * decay(.03, 70) * .3),
        "win": notes((523, 659, 784, 1047), .16, rate=6) * .6,
        "fanfare": mix(notes((523, 659, 784, 1047, 1319, 1568), .2, rate=4) * .5, notes((262, 330, 392, 523), .32, "square", 4) * .22),
    }


class SoundBoard:
    """Plays named effects through pygame.mixer; silent, never failing, when there is no audio device."""

    def __init__(self, enabled=True, volume=.6, music=.35):
        self.sounds = {}
        if not enabled:
            return
        import pygame
        from pathlib import Path
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(RATE, -16, 2, 512)
            rate, size, channels = pygame.mixer.get_init()
            pygame.mixer.set_num_channels(16)
            for name, wave in build().items():
                if rate != RATE:
                    wave = np.interp(np.arange(0, len(wave), RATE / rate), np.arange(len(wave)), wave)
                samples = (np.clip(wave / max(1, np.abs(wave).max()), -1, 1) * 32767 * volume).astype(np.int16)
                shaped = np.repeat(samples[:, None], channels, axis=1) if channels > 1 else samples
                self.sounds[name] = pygame.sndarray.make_sound(np.ascontiguousarray(shaped))
        except (pygame.error, NotImplementedError, ValueError):
            self.sounds = {}
            return
        theme = Path(__file__).resolve().parents[1] / "assets" / "music" / "theme.ogg"
        if music > 0 and theme.is_file():
            # Made by checks/build_music.py; it loops without a seam and sits under the effects.
            try:
                pygame.mixer.music.load(theme)
                pygame.mixer.music.set_volume(music)
                pygame.mixer.music.play(-1, fade_ms=1500)
            except pygame.error:
                pass

    def play(self, names):
        for name in dict.fromkeys(names):
            if name in self.sounds:
                self.sounds[name].play()
