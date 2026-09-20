"""Cut game art out of the reference images in assets/source: python checks/build_assets.py"""

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "assets"
INK = (170, 98, 88)  # sprites.INK in BGR


def soft(mask, blur=5):
    return cv2.GaussianBlur(mask, (blur, blur), 0)


def save(name, image, alpha, outline=0):
    """Crop to the alpha bounds; optionally grow an ink outline around the shape."""
    if outline:
        grown = cv2.dilate(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outline * 2 + 1, outline * 2 + 1)))
        inside = (soft(alpha).astype(np.float32) / 255)[..., None]
        image = (image * inside + np.array(INK, np.float32) * (1 - inside)).astype(np.uint8)
        alpha = grown
    ys, xs = np.where(alpha > 0)
    box = slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1)
    cv2.imwrite(str(ROOT / name), np.dstack([image[box], soft(alpha)[box]]))


def lift(image, floor):
    """Raise dark tones so projected art never reads as a physical wall (max channel < 75)."""
    return (image.astype(np.float32) * (255 - floor) / 255 + floor).astype(np.uint8)


def rock():
    image = cv2.imread(str(ROOT / "source/rock.webp"))
    mask = np.zeros((image.shape[0] + 2, image.shape[1] + 2), np.uint8)
    for seed in ((0, 0), (image.shape[1] - 1, 0), (0, image.shape[0] - 1), (image.shape[1] - 1, image.shape[0] - 1)):
        cv2.floodFill(image.copy(), mask, seed, 0, (10, 10, 10), (10, 10, 10), 8 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8))
    alpha = cv2.erode(255 - mask[1:-1, 1:-1], np.ones((3, 3), np.uint8))
    save("rock.png", lift(image, 45), alpha)


def lily_pad():
    image = cv2.imread(str(ROOT / "source/lily_pad.webp"))
    blue, green, red = [channel.astype(np.int16) for channel in cv2.split(image)]
    mask = ((green - np.maximum(red, blue)) > 25).astype(np.uint8) * 255
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
    alpha = np.zeros_like(mask)
    cv2.drawContours(alpha, [max(contours, key=cv2.contourArea)], -1, 255, -1)
    save("lily_pad.png", lift(image, 40), cv2.erode(alpha, np.ones((3, 3), np.uint8)))


def textures():
    cv2.imwrite(str(ROOT / "bark.png"), lift(cv2.imread(str(ROOT / "source/bark.webp")), 62))
    cv2.imwrite(str(ROOT / "water.jpg"), cv2.imread(str(ROOT / "source/water.webp")), [cv2.IMWRITE_JPEG_QUALITY, 90])


if __name__ == "__main__":
    rock(), lily_pad(), textures()
