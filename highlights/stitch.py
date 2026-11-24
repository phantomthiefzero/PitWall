"""Building the highlights video from the recorded clips, with ffmpeg.

1. Each clip is trimmed to its moment (its lead-in cut off by the tape time OBS started at), scaled to the first
   clip's size, given its caption (bottom left, first CAPTION_S seconds) and, with no stinger, a quick dip to black.
2. Optional title and finish cards (plain cards with text, CARD_S long).
3. The pieces are joined. A stinger:
     - with a transparent background (.webm VP9, .mov ProRes 4444 / QuickTime Animation, an animated .gif, ...): laid
       OVER each cut,
       so it hides the cut at its middle (STINGER_CUT of its length, the overlay's own built-in stingers cover
       42-58%). The video is no longer for it.
     - with no transparency: played BETWEEN clips (each one adds its length).
4. Music: the chosen tracks one after another (looped if the video is longer), under the race sound, faded out at
   the end. No music unless tracks are picked.

ffmpeg: the one in the app (imageio-ffmpeg) or one on the PATH. Text goes to ffmpeg in files (textfile=) and the
font is copied beside them, so no Windows path ever needs escaping inside a filter.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

CAPTION_S = 4.5
CARD_S = 4.0
DIP_S = 0.3
STINGER_CUT = 0.5
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\bahnschrift.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
ALPHA_FMTS = ("yuva", "rgba", "argb", "bgra", "abgr", "gbrap", "ya8", "ya16", "pal8")


class BuildError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        pass
    exe = shutil.which("ffmpeg")
    if not exe:
        raise BuildError("ffmpeg was not found (it comes with the app; reinstall it)")
    return exe


def _run(args, cwd=None, log=None):
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000          # CREATE_NO_WINDOW: no console flashing over the app
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-12:])
        if log:
            log("ffmpeg failed: %s\n%s" % (" ".join(args[:6]), tail))
        raise BuildError("ffmpeg failed:\n" + tail)
    return p


def probe(path, exe=None) -> dict:
    """Duration, size, frame rate, audio and transparency, read from ffmpeg's own description of the file."""
    exe = exe or ffmpeg_exe()
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
    p = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", **kw)
    err = p.stderr or ""
    out = {"duration": None, "w": None, "h": None, "fps": None, "audio": False, "alpha": False, "codec": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in err.splitlines():
        if "Video:" in line and out["w"] is None:
            mm = re.search(r"Video:\s*([A-Za-z0-9_]+)", line)
            out["codec"] = mm.group(1) if mm else None
            mm = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
            if mm:
                out["w"], out["h"] = int(mm.group(1)), int(mm.group(2))
            mm = re.search(r"([\d.]+)\s*fps", line)
            if mm:
                out["fps"] = float(mm.group(1))
            low = line.lower()
            out["alpha"] = any(f in low for f in ALPHA_FMTS) or "alpha_mode" in err.lower()
        if "Audio:" in line:
            out["audio"] = True
    if "alpha_mode" in err.lower() or "alpha_mode   : 1" in err:
        out["alpha"] = True
    if out["duration"] is None and not out["w"]:
        raise BuildError("ffmpeg cannot read %s" % os.path.basename(path))
    return out


def has_filter(exe, name) -> bool:
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        p = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", **kw)
    except OSError:
        return False
    return any(line.split()[1:2] == [name] for line in (p.stdout or "").splitlines() if line.strip())


def _font(work, exe=None):
    if exe is not None and not has_filter(exe, "drawtext"):
        return None                                  # this ffmpeg cannot draw text: no captions, no card text
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            dst = os.path.join(work, "font.ttf")
            shutil.copyfile(f, dst)
            return "font.ttf"
    return None


def _text_file(work, name, text):
    with open(os.path.join(work, name), "w", encoding="utf-8") as f:
        f.write(text)
    return name


def _vin(info, path):
    """Input options for a file: VP8/VP9 need libvpx to keep their transparency."""
    if info.get("codec") in ("vp9", "vp8") and info.get("alpha"):
        return ["-c:v", "lib" + ("vpx-vp9" if info["codec"] == "vp9" else "vpx"), "-i", path]
    return ["-i", path]


def _enc(opts):
    return ["-c:v", "libx264", "-preset", opts.get("preset", "veryfast"), "-crf", str(opts.get("crf", 20)),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _norm_v(W, H, fps):
    return ("scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1,fps=%s,format=yuv420p" % (W, H, W, H, fps))


def _fade_alpha(t0, t1, f=0.3):
    return ("alpha='if(lt(t,{a}),0,if(lt(t,{b}),(t-{a})/{f},if(lt(t,{c}),1,if(lt(t,{d}),({d}-t)/{f},0))))'"
            .format(a=t0, b=t0 + f, c=t1 - f, d=t1, f=f))


def segment(exe, work, src, info, out, trim, dur, W, H, fps, opts, caption=None, font=None, dip=True, game_vol=1.0):
    vf = _norm_v(W, H, fps)
    if caption and font:
        name = _text_file(work, os.path.splitext(os.path.basename(out))[0] + ".txt", caption)
        size = max(18, H // 24)
        vf += (",drawtext=fontfile=%s:textfile=%s:expansion=none:x=%d:y=h-%d:fontsize=%d:fontcolor=white:box=1:"
               "boxcolor=black@0.6:boxborderw=%d:%s" % (font, name, W // 32, size * 3, size, size // 2,
                                                         _fade_alpha(0.3, min(CAPTION_S, dur - 0.2))))
    af = "aresample=48000,aformat=channel_layouts=stereo,volume=%.3f" % game_vol
    if dip:
        vf += ",fade=t=in:st=0:d=%s,fade=t=out:st=%.3f:d=%s" % (DIP_S, max(0.0, dur - DIP_S), DIP_S)
        af += ",afade=t=in:st=0:d=%s,afade=t=out:st=%.3f:d=%s" % (DIP_S, max(0.0, dur - DIP_S), DIP_S)
    args = [exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % trim, "-t", "%.3f" % dur] + \
        _vin(info, src) + ["-f", "lavfi", "-t", "%.3f" % dur, "-i", "anullsrc=r=48000:cl=stereo"]
    a_in = "[0:a]" if info.get("audio") else "[1:a]"
    fc = "[0:v]%s[v];%s%s,apad,atrim=0:%.3f[a]" % (vf, a_in, af, dur)
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", "%.3f" % dur] + _enc(opts) + [out]
    _run(args, cwd=work, log=opts.get("_log"))
    return out


def card(exe, work, out, title, lines, W, H, fps, opts, font):
    """A plain card: a title and a few lines, faded in and out."""
    dur = CARD_S
    vf = "format=yuv420p"
    if font:
        big, small = max(24, H // 12), max(18, H // 26)
        y = H // 2 - big
        t_name = _text_file(work, os.path.splitext(os.path.basename(out))[0] + "_t.txt", title or "")
        vf += (",drawtext=fontfile=%s:textfile=%s:expansion=none:x=(w-text_w)/2:y=%d:fontsize=%d:fontcolor=white" %
               (font, t_name, y, big))
        for k, line in enumerate(lines or []):
            n = _text_file(work, os.path.splitext(os.path.basename(out))[0] + "_%d.txt" % k, line)
            vf += (",drawtext=fontfile=%s:textfile=%s:expansion=none:x=(w-text_w)/2:y=%d:fontsize=%d:fontcolor=0xC8D0DA" %
                   (font, n, y + big + small // 2 + k * int(small * 1.6) + small // 2, small))
    vf += ",fade=t=in:st=0:d=0.4,fade=t=out:st=%.2f:d=0.4" % (dur - 0.4)
    args = [exe, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x0E1116:s=%dx%d:r=%s:d=%.2f" % (W, H, fps, dur),
            "-f", "lavfi", "-t", "%.2f" % dur, "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]", "-map", "1:a", "-t", "%.2f" % dur] + _enc(opts) + [out]
    _run(args, cwd=work, log=opts.get("_log"))
    return out


def build(clips, out_path, work, opts=None, log=print, progress=None) -> dict:
    """clips: [{path, recStart, a, b, caption}] in order (failed ones left out). opts: stinger, captions, intro
    {title, lines}, outro {title, lines}, music [paths], musicVolume, gameVolume, crf, preset."""
    opts = dict(opts or {})
    opts["_log"] = log
    exe = ffmpeg_exe()
    os.makedirs(work, exist_ok=True)
    clips = [c for c in clips if c.get("ok", True) and c.get("path")]
    if not clips:
        raise BuildError("no clips to build from")
    infos = [probe(c["path"], exe) for c in clips]
    W = infos[0]["w"] or 1920
    H = infos[0]["h"] or 1080
    W, H = W - W % 2, H - H % 2
    fps = opts.get("fps") or (round(infos[0]["fps"]) if infos[0]["fps"] else 60)
    font = _font(work, exe) if (opts.get("captions", True) or opts.get("intro") or opts.get("outro")) else None
    music = [m for m in (opts.get("music") or []) if os.path.exists(m)]
    game_vol = float(opts.get("gameVolume", 0.7 if music else 1.0))
    st_path = opts.get("stinger")
    st_info = probe(st_path, exe) if st_path and os.path.exists(st_path) else None
    overlay = bool(st_info and st_info["alpha"])
    insert = bool(st_info and not st_info["alpha"])
    steps = len(clips) + 2
    done = [0]

    def step(what):
        done[0] += 1
        log(what)
        if progress is not None:
            progress(done[0], steps)

    pieces = []          # (path, duration, is_clip)
    if opts.get("intro"):
        pieces.append((card(exe, work, os.path.join(work, "intro.mp4"), opts["intro"].get("title"),
                            opts["intro"].get("lines"), W, H, fps, opts, font), CARD_S, False))
    st_seg = None
    if insert:
        st_seg = os.path.join(work, "stinger.mp4")
        segment(exe, work, st_path, st_info, st_seg, 0.0, st_info["duration"] or 1.0, W, H, fps, opts,
                dip=False, game_vol=1.0)
    for i, (c, info) in enumerate(zip(clips, infos)):
        trim = max(0.0, float(c["a"]) - float(c.get("recStart") or c["a"]))
        dur = float(c["b"]) - float(c["a"])
        if info["duration"]:
            dur = min(dur, info["duration"] - trim)
        if dur <= 0.5:
            log("clip %d is too short to use, left out" % (i + 1))
            continue
        if insert and any(p[2] for p in pieces):
            pieces.append((st_seg, st_info["duration"] or 1.0, False))
        out = os.path.join(work, "clip_%03d.mp4" % i)
        segment(exe, work, c["path"], info, out, trim, dur, W, H, fps, opts,
                caption=c.get("caption") if opts.get("captions", True) else None, font=font,
                dip=not st_info, game_vol=game_vol)
        pieces.append((out, dur, True))
        step("clip %d of %d ready" % (i + 1, len(clips)))
    if opts.get("outro"):
        pieces.append((card(exe, work, os.path.join(work, "outro.mp4"), opts["outro"].get("title"),
                            opts["outro"].get("lines"), W, H, fps, opts, font), CARD_S, False))
    # join
    lst = os.path.join(work, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p, _, _ in pieces:
            f.write("file '%s'\n" % os.path.basename(p))
    joined = os.path.join(work, "joined.mp4")
    _run([exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
          "-c", "copy", joined], cwd=work, log=log)
    real = [probe(p, exe)["duration"] or d for p, d, _ in pieces]
    total = sum(real)
    cuts, acc = [], 0.0
    for k in range(len(pieces) - 1):
        acc += real[k]
        cuts.append(acc)
    step("clips joined")
    if not overlay and not music:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        _run([exe, "-y", "-hide_banner", "-loglevel", "error", "-i", joined, "-c", "copy", "-movflags", "+faststart",
              out_path], log=log)
        step("done")
        return {"path": out_path, "duration": round(total, 1), "clips": sum(1 for p in pieces if p[2])}
    # final pass: stingers over the cuts, music under the race sound
    args = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", joined]
    fc, n_in = [], 1
    v = "[0:v]"
    a_parts = ["[0:a]"]
    if overlay:
        sl = st_info["duration"] or 1.0
        cut_at = float(opts.get("stingerCut", STINGER_CUT))
        # pixel art (a GIF, or a stinger under half the video's size) keeps hard pixel edges when scaled up
        px = ":flags=neighbor" if st_info.get("codec") == "gif" or (st_info.get("w") or W) * 2 <= W else ""
        for k, ct in enumerate(cuts):
            off = max(0.0, ct - cut_at * sl)
            args += _vin(st_info, st_path)
            fc.append("[%d:v]scale=%d:%d%s,format=yuva420p,setpts=PTS-STARTPTS+%.3f/TB[s%d]" % (n_in, W, H, px, off, k))
            fc.append("%s[s%d]overlay=eof_action=pass:format=auto[v%d]" % (v, k, k))
            v = "[v%d]" % k
            if st_info["audio"]:
                ms = int(off * 1000)
                fc.append("[%d:a]aresample=48000,aformat=channel_layouts=stereo,adelay=%d|%d[sa%d]" % (n_in, ms, ms, k))
                a_parts.append("[sa%d]" % k)
            n_in += 1
    if music:
        firsts = []
        for m in music:
            args += ["-i", m]
            fc.append("[%d:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[m%d]" % (n_in, len(firsts)))
            firsts.append("[m%d]" % len(firsts))
            n_in += 1
        mv = float(opts.get("musicVolume", 0.35))
        fade = min(4.0, total / 4)
        fc.append("%sconcat=n=%d:v=0:a=1,aloop=loop=-1:size=2147483647,atrim=0:%.3f,asetpts=N/SR/TB,"
                  "afade=t=in:st=0:d=1,afade=t=out:st=%.3f:d=%.3f,volume=%.3f[mus]"
                  % ("".join(firsts), len(firsts), total, max(0.0, total - fade), fade, mv))
        a_parts.append("[mus]")
    if len(a_parts) > 1:
        fc.append("%samix=inputs=%d:duration=first:normalize=0[aout]" % ("".join(a_parts), len(a_parts)))
        a_map = "[aout]"
    else:
        a_map = "0:a"
    fc.append("%snull[vout]" % v)
    args += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", a_map, "-t", "%.3f" % total] + _enc(opts) + \
        ["-movflags", "+faststart", out_path]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    _run(args, cwd=work, log=log)
    step("done")
    return {"path": out_path, "duration": round(total, 1), "clips": sum(1 for p in pieces if p[2])}
