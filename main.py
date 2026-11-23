"""Weekly travel video maker.

  python main.py                       # this week's destination from destinations.json
  python main.py --city Hampi --country India
  python main.py --plan-only           # just write the script, no video
  python main.py --portrait            # 1080x1920 for Shorts / Reels
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import footage  # noqa: E402
from editor import build_video  # noqa: E402
from planner import VideoPlan, make_plan  # noqa: E402
from voice import speak  # noqa: E402

HISTORY = ROOT / "output" / "history.json"


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


def record(city, folder):
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    history.append({"city": city, "date": date.today().isoformat(), "folder": str(folder)})
    HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city")
    ap.add_argument("--country", default="")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--portrait", action="store_true")
    args = ap.parse_args()

    settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    if args.portrait:
        settings["orientation"] = "portrait"

    dest = {"city": args.city, "country": args.country} if args.city else pick_destination()
    wk = date.today().isocalendar()
    slug = re.sub(r"[^a-z0-9]+", "-", dest["city"].lower()).strip("-")
    out_dir = ROOT / "output" / f"{wk.year}-W{wk.week:02d}_{slug}"
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    log(f"== {dest['city']}, {dest['country']}  ->  {out_dir}")

    # 1. Script
    plan_file = out_dir / "plan.json"
    if plan_file.exists():
        plan = VideoPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
        log("1/4 using existing plan.json (delete it to regenerate)")
    else:
        if not footage.os.getenv("GEMINI_API_KEY"):
            sys.exit("Add GEMINI_API_KEY to .env (get one at https://aistudio.google.com/apikey)")
        log("1/4 writing script with Gemini...")
        plan = make_plan(dest["city"], dest["country"], settings)
        plan_file.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    for s in plan.spots:
        log(f"    - {s.name}")
    if args.plan_only:
        return

    # 2. Footage
    if not any(map(footage.os.getenv, ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY"))):
        sys.exit("Add PEXELS_API_KEY and/or PIXABAY_API_KEY (or YOUTUBE_API_KEY) to .env")
    log("2/4 collecting footage...")
    seen, spot_clips = set(), []
    for s in plan.spots:
        log(f"  {s.name}")
        clips = footage.gather(s.search_terms, settings, work / "clips", seen, log)
        if len(clips) < 2:  # broaden the search
            clips += footage.gather([f"{s.name} {plan.city}", f"{plan.city} {plan.country}"],
                                    settings, work / "clips", seen, log)
        spot_clips.append(clips)
    if not any(spot_clips):
        sys.exit("No footage found for any place - check your API keys.")

    # 3. Voice
    log("3/4 recording voiceover...")
    voices = {"intro": speak(plan.intro_narration, work / "v_intro.mp3", settings["voice"], settings["voice_rate"]),
              "outro": speak(plan.outro_narration, work / "v_outro.mp3", settings["voice"], settings["voice_rate"])}
    for i, s in enumerate(plan.spots):
        voices[f"s{i}"] = speak(s.narration, work / f"v_s{i}.mp3", settings["voice"], settings["voice_rate"])

    # 4. Edit
    log("4/4 editing...")
    credits = [c for cl in spot_clips for c in cl]
    video = build_video(plan, spot_clips, voices, credits, settings, work, out_dir, log)

    yt = [c for c in credits if c.source == "youtube"]
    stock = sorted({c.credit for c in credits if c.source != "youtube"})
    desc = [plan.description, "", "Places in this video:"]
    desc += [f"{i + 1}. {s.name}" for i, s in enumerate(plan.spots)]
    if yt:
        desc += ["", "Creative Commons footage (CC BY):"] + [f"- {c.credit}" for c in yt]
    if stock:
        desc += ["", "Stock footage: " + ", ".join(stock)]
    desc += ["", " ".join("#" + re.sub(r"\W+", "", t) for t in plan.tags[:8])]
    (out_dir / "upload.txt").write_text(
        f"TITLE:\n{plan.title}\n\nDESCRIPTION:\n" + "\n".join(desc) + f"\n\nTAGS:\n{', '.join(plan.tags)}\n",
        encoding="utf-8")
    record(dest["city"], out_dir)
    log(f"\nDone!\n  video:     {video}\n  thumbnail: {out_dir / 'thumbnail.jpg'}\n  upload:    {out_dir / 'upload.txt'}")


if __name__ == "__main__":
    main()
