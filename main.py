"""Weekly travel video maker.

  python main.py                       # this week's destination from destinations.json
  python main.py --city Malaysia       # any city or country
  python main.py --city Hampi --country India
  python main.py --plan-only           # just write the script, no video
  python main.py --minutes 30          # long video length (default: target_minutes in settings.json)
  python main.py --no-short            # skip the weekly Short
  python main.py --no-upload           # make the videos but don't upload them
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import requests  # noqa: E402

import footage  # noqa: E402
from editor import build_video  # noqa: E402
from media import duration  # noqa: E402
from planner import SourceVideo, Spot, VideoPlan, layout, make_plan, make_plan_from_videos  # noqa: E402
from voice import speak  # noqa: E402

HISTORY = ROOT / "output" / "history.json"
CC_BY = "https://creativecommons.org/licenses/by/3.0/"


_T0 = __import__("time").monotonic()


def log(msg):  # elapsed minutes in front, so a slow step is easy to spot in the GitHub log
    m = (__import__("time").monotonic() - _T0) / 60
    print(f"[{m:5.1f}m] {msg}", flush=True)


def pick_destination():
    dests = json.loads((ROOT / "destinations.json").read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    last = {}
    for h in history:
        last[h["city"]] = h.get("date", "")
    for d in dests:
        if d["city"] not in last:
            return d
    return min(dests, key=lambda d: last[d["city"]])  # all used: the one done longest ago


def record(city, folder, url=None, short_url=None):
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    entry = {"city": city, "date": date.today().isoformat(), "folder": folder.name}
    if url:
        entry["youtube"] = url
    if short_url:
        entry["short"] = short_url
    history.append(entry)
    HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")


def write_plan(dest, settings):
    city, country = dest["city"], dest["country"]
    if settings.get("footage_mode") == "top_cc_videos":
        if not os.getenv("YOUTUBE_API_KEY"):
            log("  ! footage_mode is top_cc_videos but YOUTUBE_API_KEY is not set - using stock footage")
        else:
            query = f"{city} travel" if country in ("", city) else f"{city} {country} travel"
            log(f"  searching YouTube for the most-viewed Creative Commons videos: '{query}'")
            try:
                found = footage.top_cc_videos(query, settings.get("source_videos", 2), log=log)
            except requests.RequestException as e:
                log(f"  ! YouTube search failed: {e}")
                found = []
            if found:
                log("  Gemini is watching the source videos and writing the script...")
                try:
                    return make_plan_from_videos(city, country, [SourceVideo(**f) for f in found], settings)
                except Exception as e:  # quota, video too long, private video, ...
                    log(f"  ! Gemini couldn't use the videos ({e}) - writing a normal script")
            else:
                log("  ! no suitable Creative Commons videos found - using stock footage")
    log("  writing script with Gemini...")
    return make_plan(city, country, settings)


def chapters(plan, voices, spot_clips) -> list[str]:
    """YouTube chapters (0:00 Intro, 0:12 1. Amber Fort ...) from the real section lengths."""
    from editor import PAD
    t, out, num = 0.0, [], 0

    def stamp(s):
        s = int(s)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    marks = [(0.0, "Intro")]
    t += duration(voices["intro"]) + PAD
    for i, s in enumerate(plan.spots):
        if spot_clips[i] and f"s{i}" in voices:
            num += 1
            marks.append((t, f"{num}. {s.name}"))
            t += duration(voices[f"s{i}"]) + PAD
    marks.append((t, "Outro"))
    # YouTube ignores ALL chapters if one is under 10 s: fold a short intro into the first place,
    # and a short outro into the last one
    if len(marks) > 1 and marks[1][0] < 10:
        marks = [(0.0, marks[1][1])] + marks[2:]
    end = duration(voices["outro"]) + PAD
    if end < 10:
        marks = marks[:-1]
    out = [f"{stamp(m)} {name}" for m, name in marks]
    return out if len(out) >= 3 else []


def description(plan, credits, spot_clips=None, chapter_lines=None, settings=None, short=False):
    yt = list({c.url: c for c in credits if c.source == "youtube"}.values())  # one credit per video
    wiki = sorted({c.credit for c in credits if c.source == "wikimedia"})
    stock = sorted({c.credit for c in credits if c.source in ("pexels", "pixabay")})
    desc = [plan.description, ""]
    if chapter_lines:
        desc += ["Chapters:"] + chapter_lines
    else:
        shown = [s for i, s in enumerate(plan.spots) if spot_clips is None or spot_clips[i]]
        desc += ["Places in this video:"] + [f"{i + 1}. {s.name}" for i, s in enumerate(shown)]
    if yt:
        desc += ["", f"Footage used under Creative Commons Attribution (CC BY 3.0, {CC_BY}).",
                 "Clips were trimmed, re-edited, colour graded and given new narration. Original videos:"]
        desc += [f"- {c.credit}" for c in yt]
    if wiki:
        desc += ["", "Wikimedia Commons footage (edited):"] + [f"- {w}" for w in wiki]
    if stock:
        desc += ["", "Stock footage: " + ", ".join(stock)]
    tags = hashtags(plan, settings or {}, short)
    return "\n".join(desc + ["", " ".join(tags)])


def hashtags(plan, settings, short=False) -> list[str]:
    """Place hashtags from Gemini + the channel's own, deduplicated.

    YouTube shows the first 3 above the title, and ignores ALL hashtags if there are more than 15,
    so we keep it to about 10.
    """
    raw = list(plan.hashtags or []) or [plan.city, f"{plan.city} tour", plan.country]
    raw += settings.get("channel_hashtags", ["#TeluguTravelVlogs", "#TeluguVlogs", "#Telugu", "#Travel"])
    if short:
        raw = ["#shorts"] + raw
    out, seen = [], set()
    for t in raw:
        t = "#" + re.sub(r"[^\w]+", "", t.lstrip("#"), flags=re.UNICODE)
        if len(t) > 2 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city")
    ap.add_argument("--country", default="")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--portrait", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--minutes", type=float, help="long video length (overrides settings.json)")
    ap.add_argument("--no-short", action="store_true")
    args = ap.parse_args()

    settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    if args.minutes:
        settings["target_minutes"] = args.minutes
    if args.no_short:
        settings.setdefault("shorts", {})["enabled"] = False
    if args.portrait:
        settings["orientation"] = "portrait"
    if not os.getenv("GEMINI_API_KEY"):
        sys.exit("Add GEMINI_API_KEY to .env (get one at https://aistudio.google.com/apikey)")

    dest = {"city": args.city, "country": args.country} if args.city else pick_destination()
    slug = re.sub(r"[^a-z0-9]+", "-", dest["city"].lower()).strip("-")
    out_dir = ROOT / "output" / f"{date.today().isoformat()}_{slug}"
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    log(f"== {dest['city']}, {dest['country']}  ->  {out_dir}")

    # 1. Script
    log("1/5 script")
    plan_file = out_dir / "plan.json"
    if plan_file.exists():
        plan = VideoPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
        log("  using existing plan.json (delete it to regenerate)")
    else:
        plan = write_plan(dest, settings)
        plan_file.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    for s in plan.spots:
        shots = f"  [{len(s.segments)} shots from source videos]" if plan.sources else ""
        log(f"    - {s.name}{shots}")
    if args.plan_only:
        return

    # 2. Footage
    lay = layout(settings)
    settings["clips_per_place"] = lay["clips_per_place"]
    log(f"2/5 footage (~{lay['clips_per_place']} clips per place)")
    downloaded = footage.download_sources(plan.sources, work / "sources", log) if plan.sources else {}
    stock_ok = True  # Wikimedia Commons needs no key; Pixabay/Pexels are used when their keys are set
    if not any(map(os.getenv, ("PEXELS_API_KEY", "PIXABAY_API_KEY"))):
        log("  ! no PIXABAY_API_KEY / PEXELS_API_KEY - using Wikimedia Commons only (less footage)")
    seen, spot_clips = {s.url for s in plan.sources}, []
    # Only footage whose tags/title name the place is used, so the picture always matches the voice.
    shots = int(lay["place_secs"] / settings["cut_seconds"]) + 1
    min_items = int(settings.get("min_footage_per_place", 4))
    for i, s in enumerate(plan.spots):
        kws = footage.place_keywords(s, plan.city)
        log(f"  {s.name}  (must match: {', '.join(kws)})")
        clips = footage.segment_clips(s, plan.sources, downloaded)
        if clips:
            log(f"    {len(clips)} shots from the source videos")
        if len(clips) < lay["clips_per_place"] and stock_ok and settings.get("fill_with_stock", True):
            terms = list(dict.fromkeys(s.search_terms + [f"{s.name} {plan.city}", s.name]))
            clips += footage.gather(terms, settings, work / "clips", seen, log, keywords=kws, shots=shots)
        videos = sum(not c.is_photo for c in clips)
        if len(clips) < min_items:
            log(f"    ! only {len(clips)} matching photos/videos - leaving {s.name} out rather than "
                f"showing the wrong place")
            for c in clips:
                seen.discard(c.url)
            clips = []
        else:
            log(f"    {videos} videos + {len(clips) - videos} photos")
        spot_clips.append(clips)
    kept = sum(1 for c in spot_clips if c)
    if kept < 2:
        sys.exit("Not enough matching footage for this destination - check PIXABAY_API_KEY, or try a better-known place.")
    if kept < len(plan.spots):
        log(f"  {kept} of {len(plan.spots)} places have matching footage - the video will be shorter")

    # 3. Voice
    log("3/5 voiceover")
    voices = record_voices({"intro": plan.intro_narration, "outro": plan.outro_narration,
                            **{f"s{i}": s.narration for i, s in enumerate(plan.spots) if spot_clips[i]}},
                           work, settings)

    # 4. Edit
    log("4/5 editing the long video")
    credits = [c for cl in spot_clips for c in cl]
    video = build_video(plan, spot_clips, voices, credits, settings, work, out_dir, log)
    log(f"  length: {duration(video) / 60:.1f} min")
    desc = description(plan, credits, spot_clips, chapters(plan, voices, spot_clips), settings)
    (out_dir / "upload.txt").write_text(
        f"TITLE:\n{plan.title}\n\nDESCRIPTION:\n{desc}\n\nTAGS:\n{', '.join(plan.tags)}\n", encoding="utf-8")

    # 5. Upload
    log("5/5 upload")
    url = publish(video, out_dir / "thumbnail.jpg", plan.title, desc, plan.tags, out_dir, settings, args)

    # Bonus: the weekly Short
    short_url = None
    if settings.get("shorts", {}).get("enabled"):
        short_url = make_short(plan, spot_clips, url, out_dir / "short", settings, args)

    record(dest["city"], out_dir, url, short_url)
    log(f"\nDone!\n  video:     {video}\n  thumbnail: {out_dir / 'thumbnail.jpg'}\n  upload:    {out_dir / 'upload.txt'}"
        + (f"\n  youtube:   {url}" if url else "") + (f"\n  short:     {short_url}" if short_url else ""))


def record_voices(texts: dict, work: Path, settings: dict) -> dict:
    voices = {k: speak(t, work / f"v_{k}", settings, log) for k, t in texts.items()}
    if len({p.suffix for p in voices.values()}) > 1:  # Gemini failed half-way: redo all in one voice
        log("  re-recording everything with the Edge voice so the video has one voice")
        edge = {**settings, "tts_engine": "edge"}
        voices = {k: speak(t, work / f"v_{k}", edge, log) for k, t in texts.items()}
    return voices


def publish(video, thumbnail, title, desc, tags, folder: Path, settings, args):
    url_file = folder / "youtube_url.txt"
    if args.no_upload or not settings.get("upload", {}).get("enabled"):
        log("  upload skipped")
        return None
    if url_file.exists():
        url = url_file.read_text(encoding="utf-8").strip()
        log(f"  already uploaded: {url}")
        return url
    from youtube_upload import upload
    try:
        url = upload(video, thumbnail, title, desc, tags, settings, log)
        url_file.write_text(url, encoding="utf-8")
        return url
    except Exception as e:
        log(f"  ! upload failed: {e}\n  The video is saved - upload it by hand with upload.txt")
        return None


def make_short(plan: VideoPlan, spot_clips, long_url, folder: Path, settings, args):
    """A vertical YouTube Short teasing the long video, from its best places."""
    beats = [b for b in plan.short_beats if spot_clips[b.spot - 1]]
    if not beats or not plan.short_hook:
        log("  (no Short in this plan - skipped)")
        return None
    log(f"Short: {len(beats)} places, vertical")
    ss = {**settings, "orientation": "portrait", "cut_seconds": max(1.8, settings["cut_seconds"] * 0.7)}
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    splan = VideoPlan(
        city=plan.city, country=plan.country, title=plan.short_title or plan.title,
        intro_narration=plan.short_hook, outro_narration=plan.short_outro or plan.outro_narration,
        spots=[Spot(name=plan.spots[b.spot - 1].name, search_terms=[], narration=b.narration) for b in beats],
        description=(f"Full video: {long_url}\n\n" if long_url else "") + plan.description.split("\n\n")[0],
        tags=plan.tags, hashtags=plan.hashtags)
    clips = [spot_clips[b.spot - 1] for b in beats]
    voices = record_voices({"intro": splan.intro_narration, "outro": splan.outro_narration,
                            **{f"s{i}": s.narration for i, s in enumerate(splan.spots)}}, work, ss)
    credits = [c for cl in clips for c in cl]
    video = build_video(splan, clips, voices, credits, ss, work, folder, log)
    secs = duration(video)
    log(f"  Short length: {secs:.0f} s")
    if secs > 180:
        log("  ! longer than 3 minutes - YouTube will treat it as a normal video, not a Short")
    desc = description(splan, credits, clips, settings=settings, short=True)
    top = hashtags(splan, settings)[0] if hashtags(splan, settings) else ""
    title = f"{splan.title[:100 - len(top) - 10].strip()} {top} #shorts".replace("  ", " ")
    (folder / "upload.txt").write_text(f"TITLE:\n{title}\n\nDESCRIPTION:\n{desc}\n", encoding="utf-8")
    return publish(video, None, title, desc, plan.tags, folder, settings, args)


if __name__ == "__main__":
    main()
