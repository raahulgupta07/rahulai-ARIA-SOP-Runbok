"""One-logo white-labeling: process an uploaded logo into a square mark, favicon
and PWA icons, and extract a brand accent colour from it (Pillow). All raster work
is fail-soft — process_logo raises a clean ValueError on any error so the route can
return 400."""
import colorsys
import os
from pathlib import Path

from PIL import Image

from .config import BRAND_DIR

# whitelisted asset filenames served by GET /api/brand/asset/{name}
ASSET_NAMES = {
    "logo.png", "mark.png", "favicon.png",
    "icon-192.png", "icon-512.png", "icon-512-maskable.png",
}

_FALLBACK_ACCENT = "#c2683f"
_MAX_LOGO_W = 1024


def _ensure_dir(out_dir: str | None = None) -> Path:
    d = Path(out_dir or BRAND_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def darken(hex_color: str, frac: float = 0.18) -> str:
    """Return hex_color ~frac darker (multiply value channel). Used for accent_dk."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        hh, s, v = colorsys.rgb_to_hsv(r, g, b)
        v = max(0.0, v * (1.0 - frac))
        r, g, b = colorsys.hsv_to_rgb(hh, s, v)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    except Exception:
        return _FALLBACK_ACCENT


def derive_accent(img: "Image.Image") -> str:
    """Pick a representative brand accent from the logo.

    Ignore transparent / near-white / near-black pixels; prefer the most frequent
    colour with decent saturation and a middling value (a real brand hue). Fall
    back to the most frequent non-neutral colour, then to the default coral.
    """
    try:
        rgba = img.convert("RGBA")
        # downscale for speed; quantizing keeps the dominant hues
        small = rgba.copy()
        small.thumbnail((128, 128))
        px = small.load()
        w, h = small.size

        buckets: dict[tuple[int, int, int], int] = {}
        neutral_buckets: dict[tuple[int, int, int], int] = {}
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a < 32:  # transparent
                    continue
                hh, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                if v > 0.95 or v < 0.06:  # near-white / near-black
                    continue
                # quantize to 6 bits/channel so similar shades fold together
                key = (r >> 2 << 2, g >> 2 << 2, b >> 2 << 2)
                if s > 0.25 and 0.2 < v < 0.95:
                    buckets[key] = buckets.get(key, 0) + 1
                else:
                    neutral_buckets[key] = neutral_buckets.get(key, 0) + 1

        pool = buckets or neutral_buckets
        if not pool:
            return _FALLBACK_ACCENT
        r, g, b = max(pool.items(), key=lambda kv: kv[1])[0]
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return _FALLBACK_ACCENT


def _square_pad(img: "Image.Image") -> "Image.Image":
    """Trim the transparent bounding box then pad to a centred square on a
    transparent canvas."""
    rgba = img.convert("RGBA")
    bbox = rgba.split()[3].getbbox()  # bbox of the alpha channel
    if bbox:
        rgba = rgba.crop(bbox)
    w, h = rgba.size
    side = max(w, h) or 1
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(rgba, ((side - w) // 2, (side - h) // 2), rgba)
    return canvas


def _maskable(mark: "Image.Image", size: int = 512, scale: float = 0.8) -> "Image.Image":
    """Square mark centred at ~scale of the canvas on transparent (PWA maskable safe zone)."""
    inner = max(1, int(size * scale))
    m = mark.copy()
    m.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(m, ((size - m.width) // 2, (size - m.height) // 2), m)
    return canvas


def _resized(mark: "Image.Image", size: int) -> "Image.Image":
    m = mark.copy()
    m.thumbnail((size, size), Image.LANCZOS)
    return m


def process_logo(raw: bytes, out_dir: str | None = None) -> dict:
    """Open a raster logo, write logo.png + derived square mark/favicon/icons to
    out_dir (default BRAND_DIR) and return {"accent": "#rrggbb"}.

    Raises ValueError on any failure (caller returns 400)."""
    import io
    d = _ensure_dir(out_dir)
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        rgba = img.convert("RGBA")
    except Exception as e:  # not a readable raster image
        raise ValueError(f"could not read image: {e}")

    try:
        # 1) store original (capped width)
        orig = rgba.copy()
        if orig.width > _MAX_LOGO_W:
            ratio = _MAX_LOGO_W / float(orig.width)
            orig.thumbnail((_MAX_LOGO_W, int(orig.height * ratio)), Image.LANCZOS)
        orig.save(d / "logo.png", "PNG")

        # 2) square mark (trim transparent bbox + pad to square)
        mark = _square_pad(rgba)
        mark.save(d / "mark.png", "PNG")

        # 3) derived sizes from the square mark
        _resized(mark, 512).save(d / "icon-512.png", "PNG")
        _resized(mark, 192).save(d / "icon-192.png", "PNG")
        _resized(mark, 64).save(d / "favicon.png", "PNG")
        _maskable(mark, 512, 0.8).save(d / "icon-512-maskable.png", "PNG")

        accent = derive_accent(rgba)
        return {"accent": accent}
    except Exception as e:
        raise ValueError(f"failed to process logo: {e}")


def asset_path(name: str) -> Path | None:
    """Return the on-disk path for a whitelisted brand asset, or None."""
    if name not in ASSET_NAMES:
        return None
    p = Path(BRAND_DIR) / name
    return p if p.is_file() else None
