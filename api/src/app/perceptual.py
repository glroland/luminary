"""Perceptual (dHash) fingerprints for near-duplicate detection.

See docs/duplicate-detection.md. A 64-bit hash of what an image *looks like*,
so a resize, a re-encode or a HEIC/JPEG/PNG conversion of the same picture
lands within a few bits of the original, unlike `photos.checksum`.

Depends only on Pillow: `database.py` calls it from the startup backfill, so
it must not import `storage`/`database`/`sync`. Input is always the stored
thumbnail's JPEG bytes (already decoded + EXIF-transposed when it was made),
for new files and backfilled rows alike, so the hash is a pure function of
what's in Postgres and never needs a read from the photos backend.
"""

import io
from typing import Optional

from PIL import Image, ImageStat

HASH_COLS = 8
HASH_ROWS = 8

# Images whose 9x8 grayscale grid has a standard deviation below this are
# effectively one colour (blank page, solid fill). Their gradients are noise,
# so every such image would "match" every other: leave them unhashed.
FLAT_STDDEV = 2.0


def to_signed(value: int) -> int:
    """Postgres BIGINT is signed; fold an unsigned 64-bit value into range."""
    return value - (1 << 64) if value >= (1 << 63) else value


def to_unsigned(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def hamming(a: int, b: int) -> int:
    """Bit distance between two stored (signed or unsigned) hashes."""
    return bin(to_unsigned(a) ^ to_unsigned(b)).count("1")


def dhash(image: Image.Image) -> Optional[int]:
    """64-bit difference hash of `image` as a signed BIGINT value, or None for
    a flat image. Alpha is flattened onto white first, so a transparent PNG and
    its opaque JPEG export hash alike."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, rgba)
    grid = image.convert("L").resize((HASH_COLS + 1, HASH_ROWS), Image.LANCZOS)
    if ImageStat.Stat(grid).stddev[0] < FLAT_STDDEV:
        return None
    pixels = list(grid.get_flattened_data() if hasattr(grid, "get_flattened_data") else grid.getdata())
    bits = 0
    for row in range(HASH_ROWS):
        base = row * (HASH_COLS + 1)
        for col in range(HASH_COLS):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return to_signed(bits)


def dhash_from_bytes(data: bytes) -> Optional[int]:
    """dHash of encoded image bytes (the stored JPEG thumbnail); None if flat
    or undecodable."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return dhash(image)
    except Exception:
        return None


_CORRELATION_GRID = (32, 32)


def _gray_grid(data: bytes) -> list[float]:
    with Image.open(io.BytesIO(data)) as image:
        grid = image.convert("L").resize(_CORRELATION_GRID, Image.LANCZOS)
        return [float(v) for v in (grid.get_flattened_data() if hasattr(grid, "get_flattened_data") else grid.getdata())]


def correlation(data_a: bytes, data_b: bytes) -> float:
    """Pearson correlation (-1..1) of two encoded images' 32x32 grayscale
    grids -- a far finer check than the 64-bit hash, used to confirm a
    candidate pair. 0.0 if either is undecodable or perfectly flat."""
    try:
        a, b = _gray_grid(data_a), _gray_grid(data_b)
    except Exception:
        return 0.0
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a == 0 or var_b == 0:
        return 0.0
    return cov / (var_a * var_b) ** 0.5
