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
REPLAY_SPEED = 0.5
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


def _pixel_art(info, W) -> bool:
    """A GIF, or a stinger under half the video's width: pixel art, scaled up with hard pixel edges."""
    return bool(info) and (info.get("codec") == "gif" or (info.get("w") or W) * 2 <= W)


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


def _fade_alpha(t0, t1, f=0.3):
    return ("alpha='if(lt(t,{a}),0,if(lt(t,{b}),(t-{a})/{f},if(lt(t,{c}),1,if(lt(t,{d}),({d}-t)/{f},0))))'"
            .format(a=t0, b=t0 + f, c=t1 - f, d=t1, f=f))


def _frame_graph(src_label, W, H, fps, frame, speed=1.0, px=False):
    """The picture fitted to W x H. frame: "pad" (letterbox), "fill" (crop the middle, for a tall video from a wide
    one: the car is in the middle of iRacing's chase and TV shots) or "blur" (the whole picture, a blurred
    enlargement of it behind). Returns (graph, output label)."""
    flags = ":flags=neighbor" if px else ""
    slow = "setpts=%.4f*PTS," % (1.0 / speed) if speed != 1.0 else ""
    tail = "setsar=1,fps=%s,format=yuv420p" % fps
    if frame == "fill":
        return ("%s%sscale=%d:%d:force_original_aspect_ratio=increase%s,crop=%d:%d,%s[base]"
                % (src_label, slow, W, H, flags, W, H, tail)), "[base]"
    if frame == "blur":
        return ("%s%ssplit[fa][fb];[fa]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=28:3,"
                "eq=brightness=-0.12[bg];[fb]scale=%d:%d:force_original_aspect_ratio=decrease%s[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,%s[base]"
                % (src_label, slow, W, H, W, H, W, H, flags, tail)), "[base]"
    return ("%s%sscale=%d:%d:force_original_aspect_ratio=decrease%s,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,%s[base]"
            % (src_label, slow, W, H, flags, W, H, tail)), "[base]"


def wrap(text, width) -> str:
    """Lines of at most `width` characters, broken at spaces (drawtext does not wrap)."""
    out, line = [], ""
    for w in str(text or "").split():
        if line and len(line) + 1 + len(w) > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return "\n".join(out)


def _draw(font, name, size, x, y, alpha=None, color="white", box=True):
    """Text from a file, on a dark box (readable over any scene)."""
    d = ("drawtext=fontfile=%s:textfile=%s:expansion=none:x=%s:y=%s:fontsize=%d:fontcolor=%s:line_spacing=%d"
         % (font, name, x, y, size, color, size // 5))
    if box:
        d += ":box=1:boxcolor=black@0.6:boxborderw=%d" % (size // 2)
    if alpha:
        d += ":" + alpha
    return d


def segment(exe, work, src, info, out, trim, dur, W, H, fps, opts, caption=None, font=None, dip=True, game_vol=1.0,
            frame="pad", speed=1.0, label=None, tag=None, vertical=False, px=False):
    """One piece of the video: `dur` seconds of `src` from `trim`, played at `speed` (0.5 = half speed: the piece
    lasts dur / speed). Wide videos caption the bottom left for CAPTION_S; tall ones (vertical) put the caption at
    the top, in the part no app covers with its buttons, for the whole clip, with an optional tag line above it."""
    out_dur = dur / speed
    graph, v = _frame_graph("[0:v]", W, H, fps, frame, speed, px)
    vf = []
    stem = os.path.splitext(os.path.basename(out))[0]
    if font and vertical:
        big = max(24, W // 15)
        y = int(H * 0.115)
        if tag:
            vf.append(_draw(font, _text_file(work, stem + "_tag.txt", tag), max(16, W // 30), "(w-text_w)/2",
                            str(int(H * 0.065)), color="0xFFD27A"))
        if caption:
            vf.append(_draw(font, _text_file(work, stem + ".txt", wrap(caption, 20)), big, "(w-text_w)/2", str(y),
                            _fade_alpha(0.15, out_dur + 10, 0.25)))
        if label:
            vf.append(_draw(font, _text_file(work, stem + "_l.txt", label), big, "(w-text_w)/2", "h*0.62",
                            color="0xFFB23F"))
    elif font:
        size = max(18, H // 24)
        if caption:
            vf.append(_draw(font, _text_file(work, stem + ".txt", caption), size, str(W // 32), "h-%d" % (size * 3),
                            _fade_alpha(0.3, min(CAPTION_S, out_dur - 0.2))))
        if label:
            vf.append(_draw(font, _text_file(work, stem + "_l.txt", label), size, "w-text_w-%d" % (W // 32),
                            str(size * 2), color="0xFFB23F"))
    af = "aresample=48000,aformat=channel_layouts=stereo,volume=%.3f" % (game_vol * (0.35 if speed != 1.0 else 1.0))
    if speed != 1.0:
        af += ",atempo=%.4f" % max(0.5, min(2.0, speed))
    if dip:
        vf.append("fade=t=in:st=0:d=%s,fade=t=out:st=%.3f:d=%s" % (DIP_S, max(0.0, out_dur - DIP_S), DIP_S))
        af += ",afade=t=in:st=0:d=%s,afade=t=out:st=%.3f:d=%s" % (DIP_S, max(0.0, out_dur - DIP_S), DIP_S)
    args = [exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % trim, "-t", "%.3f" % dur] + \
        _vin(info, src) + ["-f", "lavfi", "-t", "%.3f" % out_dur, "-i", "anullsrc=r=48000:cl=stereo"]
    a_in = "[0:a]" if info.get("audio") else "[1:a]"
    fc = "%s;%s%s[v];%s%s,apad,atrim=0:%.3f[a]" % (graph, v, ",".join(vf) or "null", a_in, af, out_dur)
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", "%.3f" % out_dur] + _enc(opts) + [out]
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
    vertical = bool(opts.get("vertical"))
    if vertical:
        W, H = opts.get("size") or (1080, 1920)          # 9:16, 1080p: what Shorts, Reels and TikTok take
        frame = opts.get("frame") or "fill"
    else:
        W = infos[0]["w"] or 1920
        H = infos[0]["h"] or 1080
        frame = "pad"
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
                dip=False, game_vol=1.0, frame="fill" if vertical else "pad", px=_pixel_art(st_info, W))
    for i, (c, info) in enumerate(zip(clips, infos)):
        trim = max(0.0, float(c["a"]) - float(c.get("recStart") or c["a"]))
        dur = float(c["b"]) - float(c["a"])
        if info["duration"]:
            dur = min(dur, info["duration"] - trim)
        if dur <= 0.5:
            log("clip %d is too short to use, left out" % (i + 1))
            continue
        if insert and any(p[2] for p in pieces):
            pieces.append((st_seg, st_info["duration"] or 1.0, None))
        out = os.path.join(work, "clip_%03d.mp4" % i)
        cap = c.get("caption") if opts.get("captions", True) else None
        segment(exe, work, c["path"], info, out, trim, dur, W, H, fps, opts, caption=cap, font=font,
                dip=not st_info, game_vol=game_vol, frame=frame, tag=opts.get("tag"), vertical=vertical)
        pieces.append((out, dur, True))
        rp = c.get("replay")
        if rp and rp.get("b", 0) > rp.get("a", 0):
            # the key seconds again at half speed, straight after the clip (no stinger between: it is one moment)
            r_trim = max(0.0, float(rp["a"]) - float(c.get("recStart") or c["a"]))
            r_dur = float(rp["b"]) - float(rp["a"])
            if info["duration"]:
                r_dur = min(r_dur, info["duration"] - r_trim)
            if r_dur > 0.5:
                rout = os.path.join(work, "replay_%03d.mp4" % i)
                segment(exe, work, c["path"], info, rout, r_trim, r_dur, W, H, fps, opts, caption=cap, font=font,
                        dip=not st_info, game_vol=game_vol, frame=frame, speed=REPLAY_SPEED, label="REPLAY",
                        tag=opts.get("tag"), vertical=vertical)
                pieces[-1] = (pieces[-1][0], pieces[-1][1], "clip+")
                pieces.append((rout, r_dur / REPLAY_SPEED, False))
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
        if pieces[k][2] != "clip+":              # a clip and its own replay run on without a stinger
            cuts.append(acc)
    step("clips joined")
    if opts.get("maxS") and total > float(opts["maxS"]):
        log("the video is %.0f s, over its %.0f s limit: the end is cut" % (total, float(opts["maxS"])))
        total = float(opts["maxS"])
    if not overlay and not music:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        _run([exe, "-y", "-hide_banner", "-loglevel", "error", "-i", joined, "-t", "%.3f" % total, "-c", "copy",
              "-movflags", "+faststart", out_path], log=log)
        step("done")
        return {"path": out_path, "duration": round(total, 1), "clips": sum(1 for p in pieces if p[2] in (True, "clip+")),
            "vertical": vertical}
    # final pass: stingers over the cuts, music under the race sound
    args = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", joined]
    fc, n_in = [], 1
    v = "[0:v]"
    a_parts = ["[0:a]"]
    if overlay:
        sl = st_info["duration"] or 1.0
        cut_at = float(opts.get("stingerCut", STINGER_CUT))
        px = ":flags=neighbor" if _pixel_art(st_info, W) else ""
        fit = (":force_original_aspect_ratio=increase%s,crop=%d:%d" % (px, W, H)) if vertical else px
        for k, ct in enumerate(cuts):
            off = max(0.0, ct - cut_at * sl)
            args += _vin(st_info, st_path)
            fc.append("[%d:v]scale=%d:%d%s,format=yuva420p,setpts=PTS-STARTPTS+%.3f/TB[s%d]" % (n_in, W, H, fit, off, k))
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
    return {"path": out_path, "duration": round(total, 1), "clips": sum(1 for p in pieces if p[2] in (True, "clip+")),
            "vertical": vertical}
