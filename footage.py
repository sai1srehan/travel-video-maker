"""Finds and downloads footage that is legal to reuse.

Sources:
  * Pexels / Pixabay  - free stock video, commercial use allowed, no attribution required.
  * YouTube           - ONLY videos uploaded under Creative Commons (CC BY), filtered by the
                        API's videoLicense=creativeCommon. These must be credited, which the
                        app does automatically (on screen + in the description).
Regular (Standard YouTube License) videos are never downloaded.
"""
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from media import FFMPEG, duration

UA = {"User-Agent": "travel-video-maker/1.0"}


@dataclass
class Clip:
    path: Path
    duration: float
    source: str
    credit: str
    url: str


def _download(url: str, dest_dir: Path, prefix: str) -> Path:
    name = prefix + "_" + hashlib.md5(url.encode()).hexdigest()[:12] + ".mp4"
    path = dest_dir / name
    if path.exists() and path.stat().st_size > 0:
        return path
    with requests.get(url, stream=True, timeout=60, headers=UA) as r:
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(path)
    return path


def _pick_file(files, target_w):
    """Smallest file at least target_w wide, else the largest available."""
    files = [f for f in files if f.get("link") or f.get("url")]
    big = sorted([f for f in files if (f.get("width") or 0) >= target_w], key=lambda f: f["width"])
    if big:
        return big[0]
    return max(files, key=lambda f: f.get("width") or 0) if files else None


def search_pexels(query, n, orientation, target_w):
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.pexels.com/videos/search",
                     params={"query": query, "per_page": n, "orientation": orientation},
                     headers={"Authorization": key, **UA}, timeout=30)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        f = _pick_file([x for x in v["video_files"] if x.get("file_type") == "video/mp4"], target_w)
        if f:
            out.append(("pexels", f["link"], f"{v['user']['name']} (Pexels)", v["url"]))
    return out


def search_pixabay(query, n, target_w):
    key = os.getenv("PIXABAY_API_KEY")
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/videos/",
                     params={"key": key, "q": query[:100], "per_page": max(3, n), "safesearch": "true"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", [])[:n]:
        files = [dict(v, link=v.get("url")) for v in h["videos"].values() if v.get("url")]
        f = _pick_file(files, target_w)
        if f:
            out.append(("pixabay", f["link"], f"{h['user']} (Pixabay)", h["pageURL"]))
    return out


def search_youtube_cc(query, n, days):
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        return []
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"part": "snippet", "q": query, "type": "video", "videoLicense": "creativeCommon",
              "videoEmbeddable": "true", "maxResults": n, "order": "relevance", "key": key}
    items = []
    # First try this week's uploads, then fall back to any date.
    for extra in ({"publishedAfter": after}, {}):
        r = requests.get("https://www.googleapis.com/youtube/v3/search",
                         params={**params, **extra}, headers=UA, timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            break
    out = []
    for it in items:
        vid = it["id"]["videoId"]
        sn = it["snippet"]
        url = f"https://www.youtube.com/watch?v={vid}"
        out.append(("youtube", url, f"\"{sn['title']}\" by {sn['channelTitle']} - CC BY, {url}", url))
    return out


def _download_youtube(url: str, dest_dir: Path) -> Path | None:
    import yt_dlp
    opts = {
        "format": "bv*[height<=1080][ext=mp4]/b[ext=mp4]/b",
        "outtmpl": str(dest_dir / "yt_%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "ffmpeg_location": FFMPEG,
        "match_filter": yt_dlp.utils.match_filter_func("duration < 1500"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None
        if info.get("license") and "creative commons" not in info["license"].lower():
            return None  # double-check the licence on the video page itself
        p = Path(ydl.prepare_filename(info))
        return p if p.exists() else None


def gather(terms, settings, dest_dir: Path, seen: set, log=print) -> list[Clip]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    want = settings["clips_per_place"]
    vertical = settings["orientation"] == "portrait"
    target_w = 1080 if vertical else 1920
    clips: list[Clip] = []

    for term in terms:
        if len(clips) >= want:
            break
        candidates = []
        for fn in (lambda: search_pexels(term, 4, settings["orientation"], target_w),
                   lambda: search_pixabay(term, 4, target_w),
                   lambda: search_youtube_cc(term, 2, settings["youtube_days"])
                   if settings.get("use_youtube_cc") else []):
            try:
                candidates += fn()
            except requests.RequestException as e:
                log(f"    search error for '{term}': {e}")
        for source, link, credit, page in candidates:
            if len(clips) >= want:
                break
            if page in seen:
                continue
            try:
                path = (_download_youtube(link, dest_dir) if source == "youtube"
                        else _download(link, dest_dir, source))
            except Exception as e:  # network / extractor errors: skip this clip
                log(f"    skip {page}: {e}")
                continue
            if not path:
                continue
            d = duration(path)
            if d < 2:
                continue
            seen.add(page)
            clips.append(Clip(path, d, source, credit, page))
            log(f"    + {source}: {page} ({d:.0f}s)")
    return clips
