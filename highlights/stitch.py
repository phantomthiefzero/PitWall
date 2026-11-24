"""Building the highlights videos from the recorded clips, with ffmpeg (build() says the running order).

Words stay out of the way: a moment's line comes on TEXT_IN_S into its clip for TEXT_S and goes (a small lower third
in the wide video, the chapter in amber above it; one short line inside the apps' safe zone in a tall one), REPLAY
small in a corner. Cards (title, result) sit over a blurred, darkened still of the race.

A stinger with transparency (the built-in ones, highlights/stingers.py, or the user's .webm / .mov / .gif) is laid
OVER each cut with its middle (STINGER_CUT) on the cut; one without plays BETWEEN clips. A clip and its replays run on
without one. Music: the chosen tracks one after another (looped), under the race sound. A short (loop) ends without a
fade so it plays round.

ffmpeg: the one in the app (imageio-ffmpeg) or one on the PATH. Text goes to ffmpeg in files (textfile=) and the
font is copied beside them, so no Windows path ever needs escaping inside a filter.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

TEXT_IN_S = 0.4            # a moment's words come on this long into its clip ...
TEXT_S = 2.6               # ... and stay this long, then the picture is the race's again
SAFE_TOP = 0.16            # tall video: words start this far down (Instagram covers about the top 14%, 270 of 1920)
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


_PLAYABLE: dict = {}


def playable(path, exe=None) -> bool:
    """ffmpeg can decode the start of this audio file. A file named .mp3 can be anything (a web page saved as .mp3, a
    download cut short): the user's "storyteller.mp3", Nov 2026, failed the whole build ("Failed to find two
    consecutive MPEG audio frames"). Checked once per file version."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
    if key not in _PLAYABLE:
        exe = exe or ffmpeg_exe()
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        try:
            p = subprocess.run([exe, "-hide_banner", "-v", "error", "-i", path, "-t", "2", "-vn", "-f", "null", "-"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **kw)
            _PLAYABLE[key] = p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _PLAYABLE[key] = False
    return _PLAYABLE[key]


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


def _draw(font, name, size, x, y, alpha=None, color="white", box="black@0.5"):
    """Text from a file; box: a see-through backing (a colour@opacity) or None for a soft shadow only."""
    d = ("drawtext=fontfile=%s:textfile=%s:expansion=none:x=%s:y=%s:fontsize=%d:fontcolor=%s:line_spacing=%d"
         % (font, name, x, y, size, color, size // 5))
    if box:
        d += ":box=1:boxcolor=%s:boxborderw=%d" % (box, max(4, size // 3))
    else:
        d += ":shadowcolor=black@0.7:shadowx=2:shadowy=2"
    if alpha:
        d += ":" + alpha
    return d


def _texts(work, stem, W, H, font, vertical, caption, label, chapter, out_dur):
    """The few words a piece carries, kept out of the way: a small lower third for a moment of the wide video (on
    from TEXT_IN_S for TEXT_S, then gone), the chapter (a stint and its driver) in amber above it; in a tall video
    one short line inside the apps' safe zone (SAFE_TOP below the top, clear of the right-hand buttons); REPLAY small
    in a corner for as long as a replay runs."""
    out = []
    if not font:
        return out
    on = _fade_alpha(TEXT_IN_S, min(TEXT_IN_S + TEXT_S, out_dur - 0.1), 0.25)
    if vertical:
        size = max(22, W // 26)
        y = int(H * SAFE_TOP)
        if caption:
            out.append(_draw(font, _text_file(work, stem + ".txt", wrap(caption.upper(), 26)), size,
                             "(w-text_w)/2", str(y), on, box="black@0.45"))
        if label:
            out.append(_draw(font, _text_file(work, stem + "_l.txt", label), max(18, W // 34), "(w-text_w)/2",
                             str(y + (int(size * 2.4) if caption else 0)), color="0xFFB23F", box=None))
        return out
    size = max(16, H // 38)
    x, y = W // 24, H - H // 9
    if caption:
        out.append(_draw(font, _text_file(work, stem + ".txt", caption.upper()), size, str(x + size // 2),
                         str(y), on, box="black@0.45"))
        out.append("drawbox=x=%d:y=%d:w=%d:h=%d:color=0xFFB23F@1:t=fill:enable='between(t,%.2f,%.2f)'"
                   % (x - size // 4, y - size // 3, max(4, size // 6), int(size * 1.66), TEXT_IN_S + 0.1,
                      min(TEXT_IN_S + TEXT_S, out_dur - 0.1) - 0.1))
    if chapter:
        out.append(_draw(font, _text_file(work, stem + "_c.txt", chapter.upper()), max(14, int(size * 0.75)),
                         str(x + size // 2), str(y - int(size * 1.7)), on, color="0xFFB23F", box=None))
    if label:
        out.append(_draw(font, _text_file(work, stem + "_l.txt", label), max(14, int(size * 0.8)),
                         "w-text_w-%d" % (W // 24), str(H // 16), color="0xFFB23F", box="black@0.35"))
    return out


def segment(exe, work, src, info, out, trim, dur, W, H, fps, opts, caption=None, font=None, dip=True, game_vol=1.0,
            frame="pad", speed=1.0, label=None, vertical=False, px=False, chapter=None, dip_out=True):
    """One piece of the video: `dur` seconds of `src` from `trim`, played at `speed` (0.5 = half speed: the piece
    lasts dur / speed), with its few words (_texts)."""
    out_dur = dur / speed
    graph, v = _frame_graph("[0:v]", W, H, fps, frame, speed, px)
    stem = os.path.splitext(os.path.basename(out))[0]
    vf = _texts(work, stem, W, H, font, vertical, caption, label, chapter, out_dur)
    af = "aresample=48000,aformat=channel_layouts=stereo,volume=%.3f" % (game_vol * (0.35 if speed != 1.0 else 1.0))
    if speed != 1.0:
        af += ",atempo=%.4f" % max(0.5, min(2.0, speed))
    if dip:
        vf.append("fade=t=in:st=0:d=%s" % DIP_S)
        af += ",afade=t=in:st=0:d=%s" % DIP_S
        if dip_out:
            vf.append("fade=t=out:st=%.3f:d=%s" % (max(0.0, out_dur - DIP_S), DIP_S))
            af += ",afade=t=out:st=%.3f:d=%s" % (max(0.0, out_dur - DIP_S), DIP_S)
    args = [exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % trim, "-t", "%.3f" % dur] + \
        _vin(info, src) + ["-f", "lavfi", "-t", "%.3f" % out_dur, "-i", "anullsrc=r=48000:cl=stereo"]
    a_in = "[0:a]" if info.get("audio") else "[1:a]"
    fc = "%s;%s%s[v];%s%s,apad,atrim=0:%.3f[a]" % (graph, v, ",".join(vf) or "null", a_in, af, out_dur)
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", "%.3f" % out_dur] + _enc(opts) + [out]
    _run(args, cwd=work, log=opts.get("_log"))
    return out


def card(exe, work, out, title, lines, W, H, fps, opts, font, kicker=None, bg=None):
    """A title or result card: a small amber kicker, the title, a few lines, over a blurred, darkened still of the
    race (bg: (path, seconds)) or plain dark. Fades in and out."""
    dur = CARD_S
    stem = os.path.splitext(os.path.basename(out))[0]
    still = None
    if bg:
        still = os.path.join(work, stem + "_bg.png")
        try:
            _run([exe, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % max(0.0, bg[1]), "-i", bg[0],
                  "-frames:v", "1", still], cwd=work)
        except BuildError:
            still = None
    if still and os.path.exists(still):
        src = ["-loop", "1", "-framerate", str(fps), "-t", "%.2f" % dur, "-i", still]
        base = ("[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=24:2,"
                "eq=brightness=-0.32:saturation=0.7,setsar=1,fps=%s,format=yuv420p" % (W, H, W, H, fps))
    else:
        src = ["-f", "lavfi", "-i", "color=c=0x0E1116:s=%dx%d:r=%s:d=%.2f" % (W, H, fps, dur)]
        base = "[0:v]format=yuv420p"
    vf = [base]
    if font:
        big, small = max(26, min(W, H) // 13), max(16, min(W, H) // 34)
        y = int(H * 0.40)
        if kicker:
            vf.append(_draw(font, _text_file(work, stem + "_k.txt", kicker.upper()), small, "(w-text_w)/2",
                            str(y - int(small * 2.2)), color="0xFFB23F", box=None))
        vf.append(_draw(font, _text_file(work, stem + "_t.txt", wrap(title or "", 28 if W < H else 40)), big,
                        "(w-text_w)/2", str(y), box=None))
        n_title = len(wrap(title or "", 28 if W < H else 40).split("\n"))
        y2 = y + int(big * 1.25 * n_title) + small
        vf.append("drawbox=x=(iw-%d)/2:y=%d:w=%d:h=%d:color=0xFFB23F@1:t=fill" % (big * 2, y2 - small // 2, big * 2,
                                                                                 max(3, small // 6)))
        for k, line in enumerate(lines or []):
            vf.append(_draw(font, _text_file(work, stem + "_%d.txt" % k, wrap(line, 34 if W < H else 70)), small,
                            "(w-text_w)/2", str(y2 + small // 2 + k * int(small * 1.7)), color="0xD6DCE4", box=None))
    vf.append("fade=t=in:st=0:d=0.4,fade=t=out:st=%.2f:d=0.4" % (dur - 0.4))
    args = [exe, "-y", "-hide_banner", "-loglevel", "error"] + src + \
        ["-f", "lavfi", "-t", "%.2f" % dur, "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", ",".join(vf) + "[v]", "-map", "[v]", "-map", "1:a", "-t", "%.2f" % dur] + _enc(opts) + [out]
    _run(args, cwd=work, log=opts.get("_log"))
    return out


def _piece_of(exe, work, item, info, name, W, H, fps, opts, font, frame, vertical, game_vol, dip, **kw):
    """A recorded clip's [a, b] (cut from its recording by the tape time OBS started at) as a piece, or None."""
    trim = max(0.0, float(item["a"]) - float(item.get("recStart") or item["a"]))
    dur = float(item["b"]) - float(item["a"])
    if info["duration"]:
        dur = min(dur, info["duration"] - trim)
    if dur <= 0.5:
        return None
    speed = float(item.get("speed") or 1.0)
    out = os.path.join(work, name + ".mp4")
    segment(exe, work, item["path"], info, out, trim, dur, W, H, fps, opts, font=font, frame=frame,
            vertical=vertical, game_vol=game_vol, dip=dip, speed=speed, **kw)
    return out, dur / speed


def build(clips, out_path, work, opts=None, log=print, progress=None) -> dict:
    """The video, in this order:

        teaser      (wide, optional) the best moment's key seconds, no words: the cold open
        intro card  (optional) the event, the track, the car, the team and its drivers, where it started
        clips       in the order given; each may bring a chapter (a new stint), its own slow replay of the same
                    recording (replay) or replays from other cameras (angles); a clip and its replays run on
                    without a stinger
        outro card  (optional) the result

    clips: [{path, recStart, a, b, caption, chapter?, replay?: {a, b}, angles?: [{path, recStart, a, b, speed}]}].
    opts: vertical, frame, stinger (a path, or "builtin:<style>" from stingers.STYLES), stingerMark, stingerSub,
    captions, teaser {path, recStart, a, b}, intro / outro {kicker, title, lines}, music [paths], musicVolume,
    gameVolume, maxS, loop (no fade at the end: a short plays round), crf, preset."""
    opts = dict(opts or {})
    opts["_log"] = log
    exe = ffmpeg_exe()
    os.makedirs(work, exist_ok=True)
    clips = [c for c in clips if c.get("ok", True) and c.get("path")]
    if not clips:
        raise BuildError("no clips to build from")
    infos = {}

    def info_of(path):
        if path not in infos:
            infos[path] = probe(path, exe)
        return infos[path]
    first = info_of(clips[0]["path"])
    vertical = bool(opts.get("vertical"))
    if vertical:
        W, H = opts.get("size") or (1080, 1920)          # 9:16, 1080p: what Shorts, Reels and TikTok take
        frame = opts.get("frame") or "fill"
    else:
        W = first["w"] or 1920
        H = first["h"] or 1080
        frame = "pad"
    W, H = W - W % 2, H - H % 2
    fps = opts.get("fps") or (round(first["fps"]) if first["fps"] else 60)
    font = _font(work, exe) if (opts.get("captions", True) or opts.get("intro") or opts.get("outro")) else None
    music, skipped = [], []
    for m in opts.get("music") or []:
        (music if os.path.exists(m) and playable(m, exe) else skipped).append(m)
    for m in skipped:
        log("music: left out %s (it is not a sound file ffmpeg can play)" % os.path.basename(m))
    game_vol = float(opts.get("gameVolume", 0.7 if music else 1.0))
    st_path = opts.get("stinger")
    if isinstance(st_path, str) and st_path.startswith("builtin:"):
        from . import stingers
        try:
            st_path = stingers.render(st_path.split(":", 1)[1], W, H, fps, opts.get("stingerMark"),
                                      opts.get("stingerSub"), exe=exe)
        except Exception as e:  # noqa: BLE001
            log("the built-in stinger could not be made (%s): clips fade instead" % e)
            st_path = None
    st_info = probe(st_path, exe) if st_path and os.path.exists(st_path) else None
    overlay = bool(st_info and st_info["alpha"])
    insert = bool(st_info and not st_info["alpha"])
    dip = not st_info
    steps = len(clips) + 2
    done = [0]

    def step(what):
        done[0] += 1
        log(what)
        if progress is not None:
            progress(done[0], steps)

    pieces = []          # (path, duration, joins_next): joins_next = no stinger before the next piece
    st_seg = None
    if insert:
        st_seg = os.path.join(work, "stinger.mp4")
        segment(exe, work, st_path, st_info, st_seg, 0.0, st_info["duration"] or 1.0, W, H, fps, opts,
                dip=False, game_vol=1.0, frame="fill" if vertical else "pad", px=_pixel_art(st_info, W))

    def add(path, dur, joins=False):
        if insert and pieces and not pieces[-1][2]:
            pieces.append((st_seg, st_info["duration"] or 1.0, False))
        pieces.append((path, dur, joins))

    tz = opts.get("teaser")
    if tz and tz.get("path") and os.path.exists(tz["path"]):
        got = _piece_of(exe, work, tz, info_of(tz["path"]), "teaser", W, H, fps, opts, None, frame, vertical,
                        game_vol, dip)
        if got:
            add(*got)
    bg0 = (clips[0]["path"], max(0.0, float(clips[0]["a"]) - float(clips[0].get("recStart") or clips[0]["a"])) + 1.0)
    if opts.get("intro"):
        it = opts["intro"]
        add(card(exe, work, os.path.join(work, "intro.mp4"), it.get("title"), it.get("lines"), W, H, fps, opts, font,
                 kicker=it.get("kicker"), bg=bg0), CARD_S)
    for i, c in enumerate(clips):
        info = info_of(c["path"])
        cap = c.get("caption") if opts.get("captions", True) else None
        reps = [x for x in (c.get("angles") or []) if x.get("path") and os.path.exists(x["path"])]
        last_piece = i == len(clips) - 1 and not reps and not c.get("replay")
        got = _piece_of(exe, work, c, info, "clip_%03d" % i, W, H, fps, opts, font, frame, vertical, game_vol, dip,
                        caption=cap, chapter=c.get("chapter") if opts.get("captions", True) else None,
                        dip_out=not (last_piece and opts.get("loop")))
        if got is None:
            log("clip %d is too short to use, left out" % (i + 1))
            continue
        add(got[0], got[1], joins=bool(reps or c.get("replay")))
        if not reps and c.get("replay") and c["replay"].get("b", 0) > c["replay"].get("a", 0):
            # no other camera: the key seconds again from the same recording, at half speed
            reps = [{"path": c["path"], "recStart": c.get("recStart"), "a": c["replay"]["a"], "b": c["replay"]["b"],
                     "speed": REPLAY_SPEED}]
        for k, r in enumerate(reps):
            last = k == len(reps) - 1
            got = _piece_of(exe, work, r, info_of(r["path"]), "clip_%03d_r%d" % (i, k), W, H, fps, opts, font, frame,
                            vertical, game_vol, dip, label="REPLAY",
                            dip_out=not (last and i == len(clips) - 1 and opts.get("loop")))
            if got:
                pieces.append((got[0], got[1], not last))
            elif pieces and last:
                pieces[-1] = (pieces[-1][0], pieces[-1][1], False)
        step("clip %d of %d ready" % (i + 1, len(clips)))
    if opts.get("outro"):
        ot = opts["outro"]
        lc = clips[-1]
        bg1 = (lc["path"], max(0.0, float(lc["b"]) - float(lc.get("recStart") or lc["a"])) - 0.5)
        add(card(exe, work, os.path.join(work, "outro.mp4"), ot.get("title"), ot.get("lines"), W, H, fps, opts, font,
                 kicker=ot.get("kicker"), bg=bg1), CARD_S)
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
        if not pieces[k][2] and not insert:      # a clip and its replays run on without a stinger
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
        return {"path": out_path, "duration": round(total, 1), "clips": len(clips), "vertical": vertical,
                "skippedMusic": [os.path.basename(m) for m in skipped]}
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
        fade = min(0.6 if opts.get("loop") else 4.0, total / 4)
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
    return {"path": out_path, "duration": round(total, 1), "clips": len(clips), "vertical": vertical,
                "skippedMusic": [os.path.basename(m) for m in skipped]}
