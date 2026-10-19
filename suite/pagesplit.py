"""Serve a large single-file page in pieces, each under the stall size.

Behind security software that inspects local web traffic (seen with Norton 360), a
web reply over ~128 KB stalled at exactly 130,560 bytes about 1 time in 70 and the
page hung (see GZIP_MIN in suite/bridge.py). The Lap Analyzer stays ONE HTML file on
disk (no build step; tests run its script straight from the file), but when its
compressed size passes the target the server sends it as a small page plus a few
script pieces: the page's main <script> is replaced by a loader that fetches the
pieces, joins them and runs the result as one script, exactly as written.

Standard library only: used by the suite's own server and handed to PitWall's relay.
"""
import gzip
import hashlib
import re

TARGET = 96 * 1024          # compressed bytes per reply, well under the ~128 KB stall
PART_PREFIX = "_part"

_cache = {}


def _gz(b: bytes) -> int:
    return len(gzip.compress(b, 6, mtime=0))


def _split_lines(code: bytes, k: int):
    """k pieces of about equal size, cut only at line ends (never inside a character)."""
    size = len(code) // k
    out, start = [], 0
    for i in range(1, k):
        cut = code.find(b"\n", max(start, i * size))
        if cut < 0:
            break
        out.append(code[start:cut + 1])
        start = cut + 1
    out.append(code[start:])
    return out


def split_page(data: bytes, base: str):
    """-> (page, {name: script bytes}). `base` is the URL folder the pieces are served
    from, e.g. "/analyzer/". Small pages come back unchanged with no pieces."""
    key = (hashlib.sha1(data).hexdigest(), base)
    if key in _cache:
        return _cache[key]
    result = (data, {})
    if _gz(data) >= TARGET:
        blocks = list(re.finditer(rb"<script>([\s\S]*?)</script>", data))
        if blocks:
            m = max(blocks, key=lambda b: len(b.group(1)))
            code = m.group(1)
            k = 2
            while True:
                pieces = _split_lines(code, k)
                if all(_gz(p) < TARGET for p in pieces) or k >= 16:
                    break
                k += 1
            tag = key[0][:10]
            names = ["%s%d.%s.js" % (PART_PREFIX, i, tag) for i in range(len(pieces))]
            urls = ",".join('"%s%s"' % (base, n) for n in names)
            loader = ("<script>(async()=>{let t='';try{t=(await Promise.all([%s].map(u=>fetch(u).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.text()})))).join('')}"
                      "catch(e){document.body.insertAdjacentHTML('beforeend','<p style=\"color:#d6333a;font:16px sans-serif\">The page did not load completely ('+e.message+'). Reload to try again.</p>');return}"
                      "const s=document.createElement('script');s.textContent=t;document.body.appendChild(s)})()</script>" % urls).encode()
            page = data[:m.start()] + loader + data[m.end():]
            result = (page, dict(zip(names, pieces)))
    if len(_cache) > 8:
        _cache.clear()
    _cache[key] = result
    return result


def part(data: bytes, base: str, name: str):
    """The script piece `name` of the page `data`, or None."""
    return split_page(data, base)[1].get(name)
