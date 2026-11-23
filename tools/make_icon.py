"""Génère assets/icon.ico (et un aperçu PNG) en pur Python, sans dépendance.

Dessin : carré arrondi en dégradé bleu → cyan avec une grande étincelle blanche
(symbole « propre ») et une petite étincelle secondaire.

Usage :  python tools/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = [16, 24, 32, 48, 64, 128, 256]
SUPERSAMPLE = 4
ROOT = Path(__file__).resolve().parents[1]

TOP_LEFT = (37, 99, 235)      # #2563eb
BOTTOM_RIGHT = (6, 182, 212)  # #06b6d4


def _inside_rounded_square(x: float, y: float, radius: float) -> bool:
    """Coordonnées normalisées dans [0, 1]."""
    m = 0.04
    if x < m or x > 1 - m or y < m or y > 1 - m:
        return False
    cx = min(max(x, m + radius), 1 - m - radius)
    cy = min(max(y, m + radius), 1 - m - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def _in_sparkle(x: float, y: float, cx: float, cy: float, r: float) -> bool:
    """Astroïde : |dx|^(2/3) + |dy|^(2/3) <= r^(2/3) — une étoile à 4 branches incurvées."""
    dx, dy = abs(x - cx), abs(y - cy)
    if dx > r or dy > r:
        return False
    return dx ** (2 / 3) + dy ** (2 / 3) <= r ** (2 / 3)


def render(size: int) -> list[tuple[int, int, int, int]]:
    """Renvoie les pixels RGBA (de haut en bas) d'une icône de `size` px."""
    ss = SUPERSAMPLE
    pixels = []
    for py in range(size):
        for px in range(size):
            acc_r = acc_g = acc_b = acc_a = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) / size
                    y = (py + (sy + 0.5) / ss) / size
                    if not _inside_rounded_square(x, y, 0.22):
                        continue
                    t = (x + y) / 2
                    r = TOP_LEFT[0] + (BOTTOM_RIGHT[0] - TOP_LEFT[0]) * t
                    g = TOP_LEFT[1] + (BOTTOM_RIGHT[1] - TOP_LEFT[1]) * t
                    b = TOP_LEFT[2] + (BOTTOM_RIGHT[2] - TOP_LEFT[2]) * t
                    big = _in_sparkle(x, y, 0.46, 0.54, 0.34)
                    small = size >= 32 and _in_sparkle(x, y, 0.76, 0.25, 0.13)
                    if big or small:
                        r, g, b = 255, 255, 255
                    acc_r += r
                    acc_g += g
                    acc_b += b
                    acc_a += 255
            n = ss * ss
            if acc_a:
                cover = acc_a / 255
                pixels.append((round(acc_r / cover), round(acc_g / cover), round(acc_b / cover), round(acc_a / n)))
            else:
                pixels.append((0, 0, 0, 0))
    return pixels


def to_dib(size: int, pixels) -> bytes:
    """Image d'icône au format BMP (DIB 32 bits + masque AND), lue par toutes les versions de Windows."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):  # BMP : de bas en haut
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    mask_row_bytes = ((size + 31) // 32) * 4
    mask = bytes(mask_row_bytes * size)  # l'alpha suffit : masque entièrement transparent
    return header + b"".join(rows) + mask


def to_png(size: int, pixels) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in pixels[y * size:(y + 1) * size]) for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> None:
    images = []
    preview = None
    for size in SIZES:
        pixels = render(size)
        # 256 px en PNG (format standard des grandes icônes), le reste en BMP.
        data = to_png(size, pixels) if size == 256 else to_dib(size, pixels)
        images.append((size, data))
        if size == 256:
            preview = data

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data

    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    (out / "icon.ico").write_bytes(header + entries + blobs)
    (out / "icon.png").write_bytes(preview)
    print(f"Icône écrite : {out / 'icon.ico'}")


if __name__ == "__main__":
    main()
