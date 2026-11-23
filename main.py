"""Weekly travel video maker.

  python main.py                       # this week's destination from destinations.json
  python main.py --city Malaysia       # any city or country
  python main.py --city Hampi --country India
  python main.py --plan-only           # just write the script, no video
  python main.py --portrait            # 1080x1920 for Shorts / Reels
  python main.py --no-upload           # make the video but don't upload it
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
from planner import SourceVideo, VideoPlan, make_plan, make_plan_from_videos  # noqa: E402
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


def record(city, folder, url=None):
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    entry = {"city": city, "date": date.today().isoformat(), "folder": folder.name}
    if url:
        entry["youtube"] = url
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
    args = ap.parse_args()

    settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
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
    log("2/5 footage")
    downloaded = footage.download_sources(plan.sources, work / "sources", log) if plan.sources else {}
    stock_ok = any(map(os.getenv, ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY")))
    seen, spot_clips = {s.url for s in plan.sources}, []
    for s in plan.spots:
        log(f"  {s.name}")
        clips = footage.segment_clips(s, plan.sources, downloaded)
        if clips:
            log(f"    {len(clips)} shots from the source videos")
        if len(clips) < 2 and stock_ok and settings.get("fill_with_stock", True):
            clips += footage.gather(s.search_terms, settings, work / "clips", seen, log)
            if len(clips) < 2:  # broaden the search
                clips += footage.gather([f"{s.name} {plan.city}", f"{plan.city} {plan.country}".strip()],
                                        settings, work / "clips", seen, log)
        spot_clips.append(clips)
    if not any(spot_clips):
        sys.exit("No footage found for any place - check your API keys.")

    # 3. Voice
    log("3/5 voiceover")
    voices = {"intro": speak(plan.intro_narration, work / "v_intro", settings, log),
              "outro": speak(plan.outro_narration, work / "v_outro", settings, log)}
    for i, s in enumerate(plan.spots):
        voices[f"s{i}"] = speak(s.narration, work / f"v_s{i}", settings, log)
    if len({p.suffix for p in voices.values()}) > 1:  # Gemini failed half-way: redo all in one voice
        log("  re-recording everything with the Edge voice so the video has one voice")
        edge = {**settings, "tts_engine": "edge"}
        texts = {"intro": plan.intro_narration, "outro": plan.outro_narration,
                 **{f"s{i}": s.narration for i, s in enumerate(plan.spots)}}
        voices = {k: speak(t, work / f"v_{k}", edge, log) for k, t in texts.items()}

    # 4. Edit
    log("4/5 editing")
    credits = [c for cl in spot_clips for c in cl]
    video = build_video(plan, spot_clips, voices, credits, settings, work, out_dir, log)
    desc = description(plan, credits)
    (out_dir / "upload.txt").write_text(
        f"TITLE:\n{plan.title}\n\nDESCRIPTION:\n{desc}\n\nTAGS:\n{', '.join(plan.tags)}\n", encoding="utf-8")

    # 5. Upload
    url = None
    url_file = out_dir / "youtube_url.txt"
    if args.no_upload or not settings.get("upload", {}).get("enabled"):
        log("5/5 upload skipped")
    elif url_file.exists():
        url = url_file.read_text(encoding="utf-8").strip()
        log(f"5/5 already uploaded: {url}")
    else:
        log("5/5 uploading to YouTube")
        from youtube_upload import upload
        try:
            url = upload(video, out_dir / "thumbnail.jpg", plan.title, desc, plan.tags, settings, log)
            url_file.write_text(url, encoding="utf-8")
        except Exception as e:
            log(f"  ! upload failed: {e}\n  The video is saved - upload it by hand with upload.txt")

    record(dest["city"], out_dir, url)
    log(f"\nDone!\n  video:     {video}\n  thumbnail: {out_dir / 'thumbnail.jpg'}\n  upload:    {out_dir / 'upload.txt'}"
        + (f"\n  youtube:   {url}" if url else ""))


if __name__ == "__main__":
    main()
