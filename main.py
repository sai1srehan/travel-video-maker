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


def log(msg):
    print(msg, flush=True)


def pick_destination():
    dests = json.loads((ROOT / "destinations.json").read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    used = {h["city"] for h in history}
    for d in dests:
        if d["city"] not in used:
            return d
    week = date.today().isocalendar()
    return dests[(week.year * 53 + week.week) % len(dests)]  # all used: start cycling


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


def description(plan, credits):
    yt = list({c.url: c for c in credits if c.source == "youtube"}.values())  # one credit per video
    wiki = sorted({c.credit for c in credits if c.source == "wikimedia"})
    stock = sorted({c.credit for c in credits if c.source in ("pexels", "pixabay")})
    desc = [plan.description, "", "Places in this video:"]
    desc += [f"{i + 1}. {s.name}" for i, s in enumerate(plan.spots)]
    if yt:
        desc += ["", f"Footage used under Creative Commons Attribution (CC BY 3.0, {CC_BY}).",
                 "Clips were trimmed, re-edited, colour graded and given new narration. Original videos:"]
        desc += [f"- {c.credit}" for c in yt]
    if wiki:
        desc += ["", "Wikimedia Commons footage (edited):"] + [f"- {w}" for w in wiki]
    if stock:
        desc += ["", "Stock footage: " + ", ".join(stock)]
    desc += ["", " ".join("#" + re.sub(r"\W+", "", t) for t in plan.tags[:8])]
    return "\n".join(desc)


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
    wk = date.today().isocalendar()
    slug = re.sub(r"[^a-z0-9]+", "-", dest["city"].lower()).strip("-")
    out_dir = ROOT / "output" / f"{wk.year}-W{wk.week:02d}_{slug}"
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
    broll = []
    if stock_ok:
        log("  general footage of the destination (streets, food, transport ...)")
        broll = footage.gather(plan.broll_terms or [f"{plan.city} {plan.country}".strip()],
                               {**settings, "clips_per_place": lay["clips_per_place"] * 2},
                               work / "clips", seen, log)
    for i, s in enumerate(plan.spots):
        log(f"  {s.name}")
        clips = footage.segment_clips(s, plan.sources, downloaded)
        if clips:
            log(f"    {len(clips)} shots from the source videos")
        if len(clips) < lay["clips_per_place"] and stock_ok and settings.get("fill_with_stock", True):
            clips += footage.gather(s.search_terms, settings, work / "clips", seen, log)
            if len(clips) < 2:  # broaden the search
                clips += footage.gather([f"{s.name} {plan.city}"], settings, work / "clips", seen, log)
        missing = min(4, lay["clips_per_place"] - len(clips))
        if missing > 0 and broll:  # top up thin places with general destination footage
            clips += [broll[(i * 3 + k) % len(broll)] for k in range(min(missing, len(broll)))]
        spot_clips.append(clips)
    if not any(spot_clips):
        sys.exit("No footage found for any place - check your API keys.")

    # 3. Voice
    log("3/5 voiceover")
    voices = record_voices({"intro": plan.intro_narration, "outro": plan.outro_narration,
                            **{f"s{i}": s.narration for i, s in enumerate(plan.spots)}}, work, settings)

    # 4. Edit
    log("4/5 editing the long video")
    credits = [c for cl in spot_clips for c in cl]
    video = build_video(plan, spot_clips, voices, credits, settings, work, out_dir, log)
    log(f"  length: {duration(video) / 60:.1f} min")
    desc = description(plan, credits)
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
        tags=plan.tags)
    clips = [spot_clips[b.spot - 1] for b in beats]
    voices = record_voices({"intro": splan.intro_narration, "outro": splan.outro_narration,
                            **{f"s{i}": s.narration for i, s in enumerate(splan.spots)}}, work, ss)
    credits = [c for cl in clips for c in cl]
    video = build_video(splan, clips, voices, credits, ss, work, folder, log)
    secs = duration(video)
    log(f"  Short length: {secs:.0f} s")
    if secs > 180:
        log("  ! longer than 3 minutes - YouTube will treat it as a normal video, not a Short")
    desc = description(splan, credits) + "\n#shorts"
    title = f"{splan.title[:88]} #shorts"
    (folder / "upload.txt").write_text(f"TITLE:\n{title}\n\nDESCRIPTION:\n{desc}\n", encoding="utf-8")
    return publish(video, None, title, desc, plan.tags, folder, settings, args)


if __name__ == "__main__":
    main()
