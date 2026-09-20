"""Mix two exported songs into the looping game theme: python checks/build_music.py SONG_A SONG_B

Takes MP3, WAV, M4A or anything else soundfile or macOS afconvert can read, and writes
assets/music/theme.ogg: song A, a crossfade into song B, and a crossfade from B's end back into
A's start, so pygame can loop the file without a seam. Needs `pip install soundfile` (a build
tool only; the game itself just plays the OGG).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile

RATE = 44100
OUT = Path(__file__).resolve().parents[1] / "assets" / "music" / "theme.ogg"


def load(path):
    try:
        wave, rate = soundfile.read(path, dtype="float32", always_2d=True)
    except soundfile.LibsndfileError:
        with tempfile.TemporaryDirectory() as folder:
            decoded = Path(folder) / "decoded.wav"
            subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", str(path), str(decoded)], check=True)
            wave, rate = soundfile.read(decoded, dtype="float32", always_2d=True)
    if wave.shape[1] == 1:
        wave = np.repeat(wave, 2, axis=1)
    if rate != RATE:
        steps = np.arange(0, len(wave), rate / RATE)
        wave = np.column_stack([np.interp(steps, np.arange(len(wave)), wave[:, side]) for side in (0, 1)]).astype(np.float32)
    loud = np.abs(wave).max(axis=1) > .01
    wave = wave[np.argmax(loud):len(loud) - np.argmax(loud[::-1])]
    return wave[:, :2] * (.12 / max(1e-9, np.sqrt(np.mean(wave ** 2))))


def crossfade(first, second, seconds):
    """Equal-power, so the join neither dips nor swells."""
    count = int(min(seconds * RATE, len(first) / 3, len(second) / 3))
    ramp = np.linspace(0, np.pi / 2, count, dtype=np.float32)[:, None]
    return np.concatenate([first[:-count], first[-count:] * np.cos(ramp) + second[:count] * np.sin(ramp), second[count:]])


def theme(first, second, seconds=4.0):
    count = int(min(seconds * RATE, len(first) / 3, len(second) / 3))
    ramp = np.linspace(0, np.pi / 2, count, dtype=np.float32)[:, None]
    body = crossfade(first, second, seconds)
    # Fold the tail over the head: the file ends exactly where it begins.
    body[:count] = body[:count] * np.sin(ramp) + body[-count:] * np.cos(ramp)
    body = body[:-count]
    return body / max(1, np.abs(body).max() / .95)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mixed = theme(load(sys.argv[1]), load(sys.argv[2]))
    soundfile.write(OUT, mixed, RATE, format="OGG", subtype="VORBIS")
    print(f"{OUT} written: {len(mixed) / RATE:.0f} s, {OUT.stat().st_size / 1e6:.1f} MB")
