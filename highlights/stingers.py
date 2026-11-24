"""Highlights' own stingers, drawn frame by frame (Pillow) at the video's size, in the Broadcast overlay's styles.

Each covers the whole picture from COVER_A to COVER_B of its length (the overlay's rule, 42% to 58%), so the cut
between two clips hides in its middle (stitch.STINGER_CUT 0.5). While the picture is covered, a mark (the car
number, and the team under it) shows in the middle. Frames go into a .mov with PNG frames (transparency kept), made
once per style, size and mark and kept in the cache folder.

    wipe        a dark slab with an amber edge sweeping across, slanted
    chevrons    amber, white and dark chevrons sweeping across
    panels      eight panels dropping in one after another, then lifting away
    checker     checkered tiles flipping in from corner to corner (amber here and there)
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import sys
import tempfile

STYLES = {"wipe": "Slanted wipe", "chevrons": "Chevrons", "panels": "Panels", "checker": "Checkered tiles"}
DURATION_S = 0.9
COVER_A, COVER_B = 0.42, 0.58
DARK = (14, 17, 22, 255)
AMBER = (255, 178, 63, 255)
WHITE = (236, 240, 245, 255)


def _ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _sweep(p):
    """A band's leading edge (0 -> 1 over [0, COVER_A]) and trailing edge (0 -> 1 over [COVER_B, 1]), as a share of
    the width it must cross."""
    return _ease(p / COVER_A), _ease((p - COVER_B) / (1 - COVER_B))


def _wipe(d, p, W, H):
    slant = 0.28 * H
    span = W + slant
    lead, trail = _sweep(p)
    # the trailing edge starts well off the left (its top corner leans right): the picture stays covered to COVER_B
    x1, x0 = -slant + lead * (span + slant), -2 * slant + trail * (span + 2 * slant)
    edge = max(6, W // 60)
    if x1 <= x0:
        return
    d.polygon([(x0, H), (x0 + slant, 0), (x1 + slant, 0), (x1, H)], fill=DARK)
    d.polygon([(x1 - edge, H), (x1 - edge + slant, 0), (x1 + slant, 0), (x1, H)], fill=AMBER)
    if trail > 0:
        d.polygon([(x0, H), (x0 + slant, 0), (x0 + edge + slant, 0), (x0 + edge, H)], fill=AMBER)


def _chev_poly(x_back, x_front, W, H, depth):
    """A band between two ">" shaped edges at x_back and x_front (their tips at mid-height)."""
    m = H / 2
    return [(x_back - depth, 0), (x_front - depth, 0), (x_front, m), (x_front - depth, H), (x_back - depth, H),
            (x_back, m)]


def _chevrons(d, p, W, H):
    depth = 0.35 * H
    span = W + 2 * depth
    bands = ((AMBER, 0.0, 0.10), (WHITE, 0.05, 0.03), (DARK, 0.09, None))   # (colour, delay, width share)
    for col, delay, width in bands:
        q = min(1.0, max(0.0, (p - delay) / (1 - delay)))
        lead = _ease(q / ((COVER_A - delay) / (1 - delay)))
        trail = _ease((p - COVER_B) / (1 - COVER_B))
        front = lead * (span + depth)
        tb = -depth + trail * (span + 2 * depth)       # off the left until COVER_B, like the wipe
        back = tb if width is None else max(tb, front - width * span)
        if front > back:
            d.polygon(_chev_poly(back, front, W, H, depth), fill=col)


def _panels(d, p, W, H):
    n = 8
    w = W / n
    for k in range(n):
        delay = k * 0.012
        q = (p - delay) / (1 - 0.12)
        top = _ease(q / (0.30 / 0.88))              # down over its first part ...
        lift = _ease((q - 0.70 / 0.88) / (0.18 / 0.88))
        y1 = top * H
        y0 = lift * H
        if y1 > y0:
            x = k * w
            d.rectangle([x - 1, y0, x + w + 1, y1], fill=DARK)
            d.rectangle([x - 1, max(y0, y1 - max(5, H // 90)), x + w + 1, y1], fill=AMBER)


def _checker(d, p, W, H):
    cols, rows = (12, 7) if W >= H else (7, 12)
    tw, th = W / cols, H / rows
    span = cols + rows - 2
    for y in range(rows):
        for x in range(cols):
            delay = (x + y) / span * 0.12
            q = (p - delay) / 0.88
            s = min(_ease(q / 0.30), 1 - _ease((q - 0.70) / 0.30))
            if s <= 0:
                continue
            col = WHITE if (x + y) % 2 else DARK
            if (x * 7 + y * 3) % 23 == 0:
                col = AMBER
            cx, cy = (x + 0.5) * tw, (y + 0.5) * th
            hw, hh = tw * 0.5 * s + 1, th * 0.5 * s + 1
            d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], fill=col)


DRAW = {"wipe": _wipe, "chevrons": _chevrons, "panels": _panels, "checker": _checker}


def _font(size):
    from PIL import ImageFont
    from .stitch import FONT_CANDIDATES
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    return ImageFont.load_default()


def frame(style, p, W, H, mark=None, sub=None):
    """One frame (RGBA) at p (0..1) of the stinger."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    DRAW[style](d, p, W, H)
    if mark and COVER_A - 0.02 <= p <= COVER_B + 0.02:
        a = 1.0 - abs(p - 0.5) / (0.5 - COVER_A + 0.02)
        a = max(0.0, min(1.0, a * 1.6))
        big = _font(max(40, min(W, H) // 5))
        tw = d.textlength(mark, font=big)
        # the mark sits on a dark plate so it reads over the checker's white tiles too
        pad = min(W, H) // 30
        bh = big.size
        x, y = (W - tw) / 2, H / 2 - bh * 0.62
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        sub_h, sw = 0, 0
        small = _font(max(18, min(W, H) // 22)) if sub else None
        if sub:
            sub_h = small.size * 1.5
            sw = ld.textlength(sub, font=small)
        half = max(tw, sw) / 2 + pad * 1.5
        ld.rectangle([W / 2 - half, y - pad * 0.6, W / 2 + half, y + bh + pad * 0.6 + sub_h], fill=DARK)
        ld.text((x, y), mark, font=big, fill=AMBER)
        if sub:
            ld.text(((W - sw) / 2, y + bh + pad * 0.4), sub, font=small, fill=WHITE)
        alpha = layer.split()[3].point(lambda v: int(v * a))
        layer.putalpha(alpha)
        img = Image.alpha_composite(img, layer)
    return img


def cache_dir() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(root, "iRacingSuite", "stingers")
    os.makedirs(d, exist_ok=True)
    return d


def render(style, W, H, fps, mark=None, sub=None, exe=None, out_dir=None) -> str:
    """The stinger as a video file with transparency (.mov, PNG frames), made once and kept."""
    if style not in DRAW:
        raise ValueError("unknown stinger %r" % style)
    key = hashlib.sha1(("%s|%d|%d|%s|%s|%s|2" % (style, W, H, fps, mark, sub)).encode("utf-8")).hexdigest()[:12]
    out = os.path.join(out_dir or cache_dir(), "%s_%dx%d_%s.mov" % (style, W, H, key))
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    from .stitch import ffmpeg_exe
    exe = exe or ffmpeg_exe()
    n = max(2, int(round(DURATION_S * float(fps))))
    tmp = tempfile.mkdtemp(prefix="stinger_")
    try:
        for i in range(n):
            frame(style, i / (n - 1), W, H, mark, sub).save(os.path.join(tmp, "f%04d.png" % i), compress_level=1)
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        part = out + ".part.mov"
        p = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps), "-i",
                            os.path.join(tmp, "f%04d.png"), "-c:v", "png", "-pix_fmt", "rgba", part],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
        if p.returncode != 0:
            raise RuntimeError("could not make the stinger: %s" % (p.stderr or "").strip()[-300:])
        os.replace(part, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def covered(style, W=192, H=108, points=9) -> bool:
    """Every pixel covered at every point from COVER_A to COVER_B (for the tests)."""
    for i in range(points):
        p = COVER_A + (COVER_B - COVER_A) * i / (points - 1)
        if frame(style, p, W, H).split()[3].getextrema()[0] != 255:
            return False
    return True


def coverage(style, W=192, H=108, steps=41) -> tuple:
    """(first, last) share of the length at which every pixel is covered (for the tests)."""
    first = last = None
    for i in range(steps):
        p = i / (steps - 1)
        a = frame(style, p, W, H).split()[3]
        if a.getextrema()[0] == 255:
            first = p if first is None else first
            last = p
    return first, last
