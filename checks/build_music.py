"""Build a looping music track: python checks/build_music.py [--out NAME] SONG [SONG_B]

Takes MP3, WAV, M4A or anything else soundfile or macOS afconvert can read, and writes
assets/music/theme.ogg. One song is assumed to be cut as a loop already and is only levelled and
encoded, sample for sample. Two songs become a medley: A, a crossfade into B, and a crossfade from
B's end back into A's start, so pygame can loop the file without a seam. Needs `pip install soundfile` (a build
tool only; the game itself just plays the OGG).

--out NAME writes assets/music/NAME.ogg instead of theme.ogg, and a single song is then treated as a whole piece rather
than a ready-made loop: silence is trimmed and its end is folded over its start, so it too loops without a seam. Songs
that neither soundfile nor afconvert can decode (Opus in an M4A, say) are read with PyAV if it is installed
(`pip install av`).
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile

RATE = 44100
OUT = Path(__file__).resolve().parents[1] / "assets" / "music" / "theme.ogg"


def load(path, trim=True):
    try:
        wave, rate = soundfile.read(path, dtype="float32", always_2d=True)
    except soundfile.LibsndfileError:
        with tempfile.TemporaryDirectory() as folder:
            decoded = Path(folder) / "decoded.wav"
            try:
                subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", str(path), str(decoded)], check=True, capture_output=True)
                wave, rate = soundfile.read(decoded, dtype="float32", always_2d=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                import av
                with av.open(str(path)) as media:
                    rate = media.streams.audio[0].rate
                    resampler = av.AudioResampler(format="flt", layout="stereo", rate=rate)
                    parts = [block.to_ndarray().reshape(-1, 2) for frame in media.decode(audio=0) for block in resampler.resample(frame)]
                wave = np.concatenate(parts).astype(np.float32)
    if wave.shape[1] == 1:
        wave = np.repeat(wave, 2, axis=1)
    if rate != RATE:
        steps = np.arange(0, len(wave), rate / RATE)
        wave = np.column_stack([np.interp(steps, np.arange(len(wave)), wave[:, side]) for side in (0, 1)]).astype(np.float32)
    if trim:
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
    named = "--out" in sys.argv
    if named:
        place = sys.argv.index("--out")
        OUT = OUT.with_name(sys.argv[place + 1] + ".ogg")
        del sys.argv[place:place + 2]
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) == 3:
        mixed = theme(load(sys.argv[1]), load(sys.argv[2]))
    elif named:
        whole = load(sys.argv[1])
        count = int(3.0 * RATE)
        ramp = np.linspace(0, np.pi / 2, count, dtype=np.float32)[:, None]
        whole[:count] = whole[:count] * np.sin(ramp) + whole[-count:] * np.cos(ramp)
        mixed = whole[:-count]
        mixed = mixed / max(1, np.abs(mixed).max() / .95)
    else:
        mixed = load(sys.argv[1], trim=False)
        mixed = mixed / max(1, np.abs(mixed).max() / .95)
    with soundfile.SoundFile(OUT, "w", RATE, 2, format="OGG", subtype="VORBIS") as output:
        # libsndfile's Vorbis encoder overflows its stack on one huge buffer; feed it a second at a time.
        for start in range(0, len(mixed), RATE):
            output.write(mixed[start:start + RATE])
    print(f"{OUT} written: {len(mixed) / RATE:.0f} s, {OUT.stat().st_size / 1e6:.1f} MB")
