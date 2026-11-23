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


def make_cuts(clips, total, settings, work: Path, tag: str, rng: random.Random):
    """Render enough graded shots to cover `total` seconds.

    Like a hand-edited vlog: shot lengths vary (0.7x-1.6x cut_seconds), most shots are straight cuts,
    and only some get a slow push/pan, alternating direction.
    """
    W, H = _size(settings)
    base = settings["cut_seconds"]
    lengths, acc = [], 0.0
    while acc < total:
        L = round(rng.uniform(base * 0.7, base * 1.6), 2)
        lengths.append(L)
        acc += L
    uses = {}
    cuts = []
    for i, L in enumerate(lengths):
        c = clips[i % len(clips)]
        key = (c.path, c.start, c.end)
        k = uses.get(key, 0)
        uses[key] = k + 1
        w0, w1 = c.window
        if c.end is None:  # whole stock clip: skip its first/last 8%
            span = w1 - w0
            w0, w1 = w0 + span * 0.08, w1 - span * 0.08
        lo, hi = w0, max(w1 - L, w0)
        frac = (0.2 + k * 0.37 + rng.uniform(-0.05, 0.05)) % 1.0
        if w1 - w0 > L + 0.5:
            start = lo + frac * (hi - lo)
        else:  # short window: start at its beginning (whole short stock clip: from 0)
            start = c.start if c.end is not None else 0
        move = rng.random() < settings.get("camera_move_share", 0.35)
        if move:  # gentle drift, 4% overscan
            x = f"(iw-ow)*t/{L}" if rng.random() < 0.5 else f"(iw-ow)*(1-t/{L})"
            frame = (f"scale={_even(W * 1.04)}:{_even(H * 1.04)}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H}:x='{x}':y='(ih-oh)/2'")
        else:  # straight cut, just fill the frame
            frame = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        vf = (f"{frame},setsar=1,fps={FPS},{settings['color_grade']},"
              f"tpad=stop_mode=clone:stop_duration={L},format=yuv420p")  # hold last frame if clip is short
        out = work / f"cut_{tag}_{i:02d}.mp4"
        ffmpeg(["-ss", f"{start:.2f}", "-i", c.path, "-t", L, "-vf", vf, "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])
        cuts.append(out)
    return cuts


LOWER_THIRD_SIZE = 0.06  # font size as a fraction of the frame's short side


def _wrap(text, W, H, size):
    """Break text into lines that fit ~85% of the frame width (bold font ~0.6 em per char)."""
    max_chars = max(8, int(W * 0.85 / (H * size * 0.6)))
    return textwrap.fill(text, max_chars, break_long_words=False)


def _lower_third(textfile, H, start=0.4, end=4.5):
    a = (f"if(lt(t,{start}),0,if(lt(t,{start + 0.5}),(t-{start})/0.5,"
         f"if(lt(t,{end - 0.5}),1,if(lt(t,{end}),({end}-t)/0.5,0))))")
    return (f"drawtext=fontfile=font.ttf:textfile={textfile}:expansion=none:"
            f"fontsize={int(H * LOWER_THIRD_SIZE)}:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=24:"
            f"line_spacing=10:x=w*0.05:y=h-th-h*0.1:alpha='{a}'")


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
    prepare_font(work, settings)
    rng = random.Random(plan.city)
    segments = []

    # Intro: one shot from each place under the title
    intro_pool = [cl[0] for cl in spot_clips if cl] or [c for cl in spot_clips for c in cl]
    total = duration(voices["intro"]) + PAD
    cuts = make_cuts(intro_pool, total, settings, work, "intro", rng)
    t1 = _write_text(work, "t_title.txt", plan.city.upper())
    t2 = _write_text(work, "t_sub.txt", plan.country)
    segments.append(make_segment(cuts, voices["intro"], [
        _centered(t1, H, 0.12, "(h-th)/2-h*0.05"), _centered(t2, H, 0.05, "(h/2)+h*0.06", 0.8)],
        work, "intro", settings))
    log("  intro done")

    for i, (spot, clips) in enumerate(zip(plan.spots, spot_clips)):
        if not clips:
            log(f"  ! no footage for {spot.name}, skipped")
            continue
        tag = f"s{i}"
        total = duration(voices[tag]) + PAD
        cuts = make_cuts(clips, total, settings, work, tag, rng)
        short = min(W, H)  # size by the short side so portrait titles aren't oversized
        tf = _write_text(work, f"t_{tag}.txt", _wrap(f"{i + 1}. {spot.name}", W, short, LOWER_THIRD_SIZE))
        segments.append(make_segment(cuts, voices[tag], [_lower_third(tf, short)], work, tag, settings))
        log(f"  {spot.name} done")

    # Outro with credits
    all_clips = [c for cl in spot_clips for c in cl]
    total = duration(voices["outro"]) + PAD
    cuts = make_cuts(all_clips[-3:] or all_clips, total, settings, work, "outro", rng)
    names = sorted({c.credit.split(" - ")[0] if c.source in ("youtube", "wikimedia") else c.credit
                    for c in credits})
    shown = names[:6] + ([f"+ {len(names) - 6} more"] if len(names) > 6 else [])
    tc = _write_text(work, "t_credits.txt", "Footage\n" + "\n".join(shown) + "\n\nFull credits in description")
    segments.append(make_segment(cuts, voices["outro"], [
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.45:t=fill",
        _centered(tc, H, 0.032, "(h-th)/2", 0.2)], work, "outro", settings))
    log("  outro done")

    # Join sections
    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    joined = work / "joined.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name], cwd=work)

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

    # Thumbnail
    tt = _write_text(work, "t_thumb.txt", plan.city.upper())
    shot = next((p for p in sorted(work.glob("cut_s*_00.mp4"))), work / "cut_intro_00.mp4")
    ffmpeg(["-ss", "1.5", "-i", shot, "-frames:v", "1",
            "-vf", f"drawtext=fontfile=font.ttf:textfile={tt}:expansion=none:fontsize={int(H * 0.16)}:"
                   f"fontcolor=yellow:borderw=8:bordercolor=black:x=(w-tw)/2:y=h-th-h*0.12",
            "-q:v", "2", (out_dir / "thumbnail.jpg").resolve()], cwd=work)
    return final
