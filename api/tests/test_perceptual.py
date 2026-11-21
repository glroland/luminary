import io

from PIL import Image, ImageDraw

from app import perceptual


def _scene(size=(640, 480)) -> Image.Image:
    img = Image.new("RGB", size, (30, 60, 120))
    d = ImageDraw.Draw(img)
    w, h = size
    d.rectangle([w * 0.1, h * 0.1, w * 0.5, h * 0.6], fill=(220, 200, 40))
    d.ellipse([w * 0.5, h * 0.4, w * 0.9, h * 0.9], fill=(200, 30, 30))
    d.line([0, h, w, 0], fill=(255, 255, 255), width=max(2, w // 60))
    return img


def _other() -> Image.Image:
    img = Image.new("RGB", (640, 480), (240, 240, 240))
    d = ImageDraw.Draw(img)
    for i in range(0, 640, 40):
        d.rectangle([i, 0, i + 15, 480 - i // 2], fill=(10, 90, 10))
    d.ellipse([20, 300, 200, 460], fill=(0, 0, 0))
    return img


def _roundtrip(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return Image.open(io.BytesIO(buf.getvalue()))


def test_identical_image_distance_zero():
    assert perceptual.hamming(perceptual.dhash(_scene()), perceptual.dhash(_scene())) == 0


def test_resizes_and_reencodes_are_close():
    base = perceptual.dhash(_scene())
    variants = [
        _scene().resize((320, 240), Image.LANCZOS),
        _scene().resize((160, 120), Image.BILINEAR),
        _roundtrip(_scene(), "JPEG", quality=30),
        _roundtrip(_scene(), "PNG"),
    ]
    for v in variants:
        assert perceptual.hamming(base, perceptual.dhash(v)) <= 6


def test_different_image_is_far():
    assert perceptual.hamming(perceptual.dhash(_scene()), perceptual.dhash(_other())) >= 16


def test_flat_image_is_not_hashed():
    assert perceptual.dhash(Image.new("RGB", (100, 100), (5, 5, 5))) is None


def test_transparent_png_matches_flattened_jpeg():
    rgba = _scene().convert("RGBA")
    assert perceptual.hamming(perceptual.dhash(rgba), perceptual.dhash(_scene())) <= 2


def test_hash_fits_signed_bigint_and_bytes_helper():
    for img in (_scene(), _other()):
        value = perceptual.dhash(img)
        assert -(1 << 63) <= value < (1 << 63)
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        assert perceptual.dhash_from_bytes(buf.getvalue()) is not None
    assert perceptual.dhash_from_bytes(b"not an image") is None


def test_banded_candidate_search_matches_brute_force():
    import random

    from app import storage

    rng = random.Random(7)
    base = [rng.getrandbits(64) for _ in range(40)]
    values = list(base)
    for b in base[:20]:  # near-copies at a few distances
        flipped = b
        for bit in rng.sample(range(64), rng.randint(0, 8)):
            flipped ^= 1 << bit
        values.append(flipped)
    rows = [{"phash": perceptual.to_signed(v)} for v in values]
    for max_distance in (0, 3, 6, 10):
        expected = {
            (i, j): bin(values[i] ^ values[j]).count("1")
            for i in range(len(values))
            for j in range(i + 1, len(values))
            if bin(values[i] ^ values[j]).count("1") <= max_distance
        }
        assert storage._candidate_pairs(rows, max_distance) == expected


def test_correlation_high_for_resize_low_for_different():
    def jpeg(img):
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return buf.getvalue()

    a = jpeg(_scene())
    assert perceptual.correlation(a, jpeg(_scene().resize((160, 120)))) > 0.95
    assert perceptual.correlation(a, jpeg(_other())) < 0.5
    assert perceptual.correlation(a, b"junk") == 0.0
