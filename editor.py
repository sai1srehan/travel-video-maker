"""Cuts footage into short highlight shots, adds titles, voiceover and music.

House style:
  * quick 3-second cuts taken from the middle of each clip (skips intros/outros of source videos)
  * slow left/right pan on every shot, colour grade, short fade between sections
  * place name lower-third that fades in and out
"""
import math
import random
import shutil
import textwrap
from pathlib import Path

from media import duration, ffmpeg

FPS = 30
PAD = 0.6  # seconds of breathing room after each narration block


def _size(settings):
    return (1080, 1920) if settings["orientation"] == "portrait" else (1920, 1080)


def _even(x):
    return int(math.ceil(x / 2) * 2)


def _write_text(work: Path, name: str, text: str) -> str:
    (work / name).write_text(text, encoding="utf-8")
    return name


def prepare_font(work: Path, settings):
    for f in (settings.get("font_file"), "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # GitHub Actions / Ubuntu
              str(Path(__file__).parent / "assets" / "font.ttf")):
        if f and Path(f).exists():
            shutil.copy(f, work / "font.ttf")
            return
    raise FileNotFoundError("No font found - set font_file in settings.json")


def _trimmed(c):
    w0, w1 = c.window
    if c.end is None:  # whole stock clip: skip its first/last 8%
        span = w1 - w0
        w0, w1 = w0 + span * 0.08, w1 - span * 0.08
    return w0, w1


def _free_start(c, L, used, rng):
    """A start time in clip c whose [start, start+L] hasn't been used yet, or None."""
    key = str(c.path)
    taken = used.setdefault(key, [])
    if getattr(c, "is_photo", False):  # each photo is shown once
        return None if taken else 0.0
    w0, w1 = _trimmed(c)
    if w1 - w0 < L + 0.3:  # short clip: usable once, from its beginning
        return None if taken else (c.start if c.end is not None else 0.0)
    slots = list(range(int((w1 - w0) // L)))
    rng.shuffle(slots)
    for j in slots:
        s = w0 + j * L
        if all(s + L <= a or s >= b for a, b in taken):
            return s
    return None


def make_cuts(clips, total, settings, work: Path, tag: str, rng: random.Random, used: dict | None = None):
    """Render enough graded shots to cover `total` seconds.

    Like a hand-edited vlog: shot lengths vary (0.7x-1.6x cut_seconds), most shots are straight cuts,
    and only some get a slow drift. `used` tracks which parts of each clip were already shown
    (shared across the whole video), so the same footage isn't repeated until everything is used.
    """
    W, H = _size(settings)
    base = settings["cut_seconds"]
    used = {} if used is None else used
    lengths, acc = [], 0.0
    while acc < total:
        L = round(rng.uniform(base * 0.7, base * 1.6), 2)
        lengths.append(L)
        acc += L
    cuts = []
    n = len(clips)
    for i, L in enumerate(lengths):
        pick = None
        for c in clips[i % n:] + clips[:i % n]:  # round-robin, first clip with unused footage
            s = _free_start(c, L, used, rng)
            if s is not None:
                pick = (c, s)
                break
        if pick is None:  # everything already shown once: reuse a random part
            c = clips[i % n]
            w0, w1 = _trimmed(c)
            pick = (c, w0 + rng.random() * max(0.0, w1 - w0 - L))
        c, start = pick
        used.setdefault(str(c.path), []).append((start, start + L))
        out = work / f"cut_{tag}_{i:03d}.mp4"
        if getattr(c, "is_photo", False):
            _photo_cut(c.path, L, W, H, settings, rng, out)
            cuts.append(out)
            continue
        move = rng.random() < settings.get("camera_move_share", 0.35)
        if move:  # gentle drift, 4% overscan
            x = f"(iw-ow)*t/{L}" if rng.random() < 0.5 else f"(iw-ow)*(1-t/{L})"
            frame = (f"scale={_even(W * 1.04)}:{_even(H * 1.04)}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H}:x='{x}':y='(ih-oh)/2'")
        else:  # straight cut, just fill the frame
            frame = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        vf = (f"{frame},setsar=1,fps={FPS},{settings['color_grade']},"
              f"tpad=stop_mode=clone:stop_duration={L},format=yuv420p")  # hold last frame if clip is short
        ffmpeg(["-ss", f"{start:.2f}", "-i", c.path, "-t", L, "-vf", vf, "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])
        cuts.append(out)
    return cuts


def _photo_cut(img, L, W, H, settings, rng, out):
    """A still photo as a moving shot (slow zoom in/out or pan), like a vlogger's photo montage."""
    n = max(1, int(round(L * FPS)))
    style = rng.choice(["in", "out", "left", "right"])
    if style in ("in", "out"):
        z = f"1+0.08*on/{n}" if style == "in" else f"1.08-0.08*on/{n}"
        # upscale first so the zoom is smooth instead of jumping pixel by pixel
        frame = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                 f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
    else:
        x = f"(iw-ow)*t/{L}" if style == "right" else f"(iw-ow)*(1-t/{L})"
        frame = (f"scale={_even(W * 1.1)}:{_even(H * 1.1)}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H}:x='{x}':y='(ih-oh)/2'")
    vf = f"{frame},setsar=1,fps={FPS},{settings['color_grade']},format=yuv420p"
    ffmpeg(["-loop", "1", "-framerate", FPS, "-t", L, "-i", img, "-vf", vf, "-frames:v", n, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])


LOWER_THIRD_SIZE = 0.06  # font size as a fraction of the frame's short side


def _wrap(text, W, H, size):
    """Break text into lines that fit ~85% of the frame width (bold font ~0.6 em per char)."""
    max_chars = max(8, int(W * 0.85 / (H * size * 0.6)))
    return textwrap.fill(text, max_chars, break_long_words=False)


def _lower_third(textfile, H, start=0.4, end=4.5, bottom=0.1):
    a = (f"if(lt(t,{start}),0,if(lt(t,{start + 0.5}),(t-{start})/0.5,"
         f"if(lt(t,{end - 0.5}),1,if(lt(t,{end}),({end}-t)/0.5,0))))")
    return (f"drawtext=fontfile=font.ttf:textfile={textfile}:expansion=none:"
            f"fontsize={int(H * LOWER_THIRD_SIZE)}:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=24:"
            f"line_spacing=10:x=w*0.05:y=h-th-h*{bottom}:alpha='{a}'")


def _centered(textfile, H, size=0.08, y="(h-th)/2", start=0.3, end=None):
    enable = f":enable='gte(t,{start})'" if end is None else f":enable='between(t,{start},{end})'"
    return (f"drawtext=fontfile=font.ttf:textfile={textfile}:expansion=none:"
            f"fontsize={int(H * size)}:fontcolor=white:borderw=4:bordercolor=black@0.7:"
            f"line_spacing=12:x=(w-tw)/2:y={y}{enable}")


def make_segment(cuts, voice_mp3: Path, overlays: list[str], work: Path, tag: str, settings) -> Path:
    W, H = _size(settings)
    D = duration(voice_mp3) + PAD
    lst = work / f"cuts_{tag}.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in cuts), encoding="utf-8")
    vf = ",".join(overlays + [f"fade=in:st=0:d=0.3,fade=out:st={D - 0.3:.2f}:d=0.3"])
    out = work / f"seg_{tag}.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst.name, "-i", voice_mp3.name,
            "-filter_complex", f"[0:v]{vf}[v];[1:a]apad,atrim=0:{D:.2f},aresample=48000[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{D:.2f}", "-r", FPS,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", out.name], cwd=work)
    return out


def _pick_music(settings, rng):
    """A random track from assets/music/ (different each week), else settings['music_file']."""
    here = Path(__file__).parent
    folder = here / "assets" / "music"
    tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")) \
        if folder.is_dir() else []
    if tracks:
        return rng.choice(tracks)
    f = Path(settings.get("music_file") or "")
    f = f if f.is_absolute() else here / f
    return f if f.is_file() else None


def build_video(plan, spot_clips, voices, credits, settings, work: Path, out_dir: Path, log=print):
    """spot_clips: list[list[Clip]] aligned with plan.spots; voices: dict tag -> mp3 path."""
    W, H = _size(settings)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepare_font(work, settings)
    rng = random.Random(plan.city + out_dir.name)
    used = {}  # footage already shown, shared by all sections
    segments = []
    thumb_src = work / "thumb_src.jpg"

    def section(clips, voice, overlays, tag):
        cuts = make_cuts(clips, duration(voice) + PAD, settings, work, tag, rng, used)
        if not thumb_src.exists() and tag.startswith("s"):  # first place's first shot -> thumbnail
            ffmpeg(["-ss", "1", "-i", cuts[0], "-frames:v", "1", "-q:v", "2", thumb_src])
        segments.append(make_segment(cuts, voice, overlays, work, tag, settings))
        for c in cuts:  # free disk space as we go (long videos have hundreds of shots)
            c.unlink(missing_ok=True)

    # Intro: one shot from each place under the title
    intro_pool = [cl[0] for cl in spot_clips if cl] or [c for cl in spot_clips for c in cl]
    title = [_centered(_write_text(work, "t_title.txt", plan.city.upper()), H, 0.12, "(h-th)/2-h*0.05")]
    if plan.country and plan.country.lower() != plan.city.lower():
        title.append(_centered(_write_text(work, "t_sub.txt", plan.country), H, 0.05, "(h/2)+h*0.06", 0.8))
    section(intro_pool, voices["intro"], title, "intro")
    log("  intro done")

    num = 0
    for i, (spot, clips) in enumerate(zip(plan.spots, spot_clips)):
        tag = f"s{i}"
        if not clips or tag not in voices:
            log(f"  ! no footage for {spot.name}, skipped")
            continue
        num += 1  # on-screen number stays 1, 2, 3 ... even when a place was left out
        short = min(W, H)  # size by the short side so portrait titles aren't oversized
        tf = _write_text(work, f"t_{tag}.txt", _wrap(f"{num}. {spot.name}", W, short, LOWER_THIRD_SIZE))
        bottom = 0.3 if H > W else 0.1  # Shorts: keep clear of YouTube's caption/channel overlay
        section(clips, voices[tag], [_lower_third(tf, short, bottom=bottom)], tag)
        log(f"  {spot.name} done ({i + 1}/{len(plan.spots)})")

    # Outro with credits
    all_clips = [c for cl in spot_clips for c in cl]
    names = sorted({c.credit.split(" - ")[0] if c.source in ("youtube", "wikimedia") else c.credit
                    for c in credits})
    shown = names[:6] + ([f"+ {len(names) - 6} more"] if len(names) > 6 else [])
    tc = _write_text(work, "t_credits.txt", "Footage\n" + "\n".join(shown) + "\n\nFull credits in description")
    section(all_clips, voices["outro"], ["drawbox=x=0:y=0:w=iw:h=ih:color=black@0.45:t=fill",
                                         _centered(tc, H, 0.032, "(h-th)/2", 0.2)], "outro")
    log("  outro done")

    # Join sections
    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    joined = work / "joined.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name], cwd=work)
    for s in segments:
        s.unlink(missing_ok=True)

    final = out_dir / "video.mp4"
    music = _pick_music(settings, rng)
    if music:
        log(f"  music: {music.name}")
        vol = settings.get("music_volume", 0.12)
        total_d = duration(joined)
        ffmpeg(["-i", joined, "-stream_loop", "-1", "-i", music, "-filter_complex",
                f"[1:a]volume={vol},afade=out:st={max(total_d - 3, 0):.2f}:d=3[m];"
                f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", final])
    else:
        log("  (no music found - add royalty-free tracks to assets/music/ for background music)")
        shutil.copy(joined, final)
    joined.unlink(missing_ok=True)

    # Thumbnail
    if thumb_src.exists():
        tt = _write_text(work, "t_thumb.txt", _wrap(plan.city.upper(), W, min(W, H), 0.16))
        ffmpeg(["-i", thumb_src.name, "-frames:v", "1",
                "-vf", f"drawtext=fontfile=font.ttf:textfile={tt}:expansion=none:fontsize={int(min(W, H) * 0.16)}:"
                       f"fontcolor=yellow:borderw=8:bordercolor=black:x=(w-tw)/2:y=h-th-h*0.12",
                "-q:v", "2", (out_dir / "thumbnail.jpg").resolve()], cwd=work)
    return final
