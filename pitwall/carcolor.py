"""One colour per car, for the dots on PitWall's map and the spotter.

The car's paint as people see it on track: taken from iRacing's own picture of
the car (pitwall/carrender.py), which carries the Trading Paints livery when the
driver has one. Without a picture, the first colour of iRacing's paint scheme
(CarDesignStr "pattern,col1,col2,col3") stands in.

A colour is not a number shown to anyone; it picks a dot's fill. Standard library
only (the bridge runs on the sim PC with nothing else installed).
"""
from __future__ import annotations

import colorsys
import struct
import zlib


def png_pixels(data: bytes, step: int = 4):
    """(width, height, [(r, g, b, a)]) of an 8-bit PNG, every `step`th pixel of every
    `step`th row. Truecolor, truecolor+alpha, grey, grey+alpha and palette images;
    not interlaced. Raises ValueError for anything else."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, pal, trns = 8, [], None, None
    w = h = depth = ctype = interlace = None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            pal = [tuple(body[i:i + 3]) for i in range(0, len(body) - 2, 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if w is None or depth != 8 or interlace:
        raise ValueError("unsupported PNG")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if bpp is None:
        raise ValueError("unsupported PNG colour type")
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    prev = bytearray(stride)
    out = []
    p = 0
    for y in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if y % step:
            continue
        for x in range(0, w, step):
            q = x * bpp
            if ctype == 6:
                out.append(tuple(line[q:q + 4]))
            elif ctype == 2:
                out.append((line[q], line[q + 1], line[q + 2], 255))
            elif ctype == 0:
                out.append((line[q], line[q], line[q], 255))
            elif ctype == 4:
                out.append((line[q], line[q], line[q], line[q + 1]))
            else:
                k = line[q]
                r, g, b = pal[k] if pal and k < len(pal) else (0, 0, 0)
                a = trns[k] if trns and k < len(trns) else 255
                out.append((r, g, b, a))
    return w, h, out


def main_color(pixels) -> str | None:
    """The colour most of the car is painted: the biggest group of similar colours among
    opaque pixels, preferring real colours over the greys of tyres, glass and shadow. A car
    painted mostly black, white or silver comes out that way."""
    groups: dict[tuple, list] = {}
    grey: dict[int, list] = {}
    n = 0
    for r, g, b, a in pixels:
        if a < 200:
            continue
        n += 1
        hh, ss, vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if ss < 0.25 or vv < 0.18:
            k = int(vv * 8)
            grey.setdefault(k, [0, 0, 0, 0])
            acc = grey[k]
        else:
            k = (int(hh * 24) % 24, int(vv * 4))
            groups.setdefault(k, [0, 0, 0, 0])
            acc = groups[k]
        acc[0] += 1
        acc[1] += r
        acc[2] += g
        acc[3] += b
    if not n:
        return None
    best = max(groups.values(), key=lambda v: v[0], default=None)
    # a coloured livery: its biggest colour, if it covers a fair part of the car
    if best and best[0] >= 0.12 * n:
        acc = best
    else:
        # black, white or silver: the biggest grey, but not the darkest band (tyres, glass)
        cand = {k: v for k, v in grey.items() if k > 0} or grey
        acc = max(cand.values(), key=lambda v: v[0]) if cand else best
        if acc is None:
            return None
    c = acc[0]
    return "#%02x%02x%02x" % (acc[1] // c, acc[2] // c, acc[3] // c)


def png_color(data: bytes, step: int = 4) -> str | None:
    try:
        w, h, px = png_pixels(data, step)
    except (ValueError, zlib.error, struct.error, IndexError):
        return None
    # A picture with an opaque backdrop: drop whatever colour fills its edges.
    cols = -(-w // step)
    rows = len(px) // cols if cols else 0
    edge = [px[i] for i in range(cols)] + [px[(rows - 1) * cols + i] for i in range(cols)] if rows else []
    edge = [q for q in edge if q[3] >= 200]
    if len(edge) > cols // 2:
        r0 = sum(q[0] for q in edge) / len(edge)
        g0 = sum(q[1] for q in edge) / len(edge)
        b0 = sum(q[2] for q in edge) / len(edge)
        if all(abs(q[0] - r0) + abs(q[1] - g0) + abs(q[2] - b0) < 40 for q in edge):
            px = [q for q in px if abs(q[0] - r0) + abs(q[1] - g0) + abs(q[2] - b0) >= 40]
    return main_color(px)


def design_color(design: str | None) -> str | None:
    """iRacing's paint scheme "pattern,col1,col2,col3" (hex without '#'): its first colour."""
    if not design:
        return None
    parts = str(design).split(",")
    if len(parts) < 2:
        return None
    c = parts[1].strip().lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return "#" + c.lower()
    return None
