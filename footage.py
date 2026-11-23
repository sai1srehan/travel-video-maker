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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from media import FFMPEG, duration

UA = {"User-Agent": "travel-video-maker/1.0 (automated weekly travel video tool; python-requests)"}


@dataclass
class Clip:
    path: Path
    duration: float
    source: str
    credit: str
    url: str
    start: float = 0.0          # usable window inside the file (seconds)
    end: float | None = None    # None = to the end

    @property
    def window(self):
        return self.start, (self.end if self.end is not None else self.duration)


def yt_credit(title, channel, url):
    return f"\"{title}\" by {channel} - CC BY, {url}"


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


def _commercial_ok(lic: str) -> bool:
    """Licences that allow commercial reuse without share-alike: CC0, public domain, CC BY."""
    lic = lic.strip().lower()
    if lic in ("cc0", "pdm", "public domain") or lic.startswith(("cc0", "public domain", "pd")):
        return True
    return lic.startswith("cc by") and not any(x in lic for x in ("-sa", "-nc", "-nd", " sa", " nc", " nd"))


def search_wikimedia(query, n, target_w):
    """Free-licence travel videos from Wikimedia Commons (official API, works from GitHub servers)."""
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"filetype:video {query}", "gsrlimit": 12,
        "prop": "videoinfo", "viprop": "url|size|mime|extmetadata|derivatives"})
    r.raise_for_status()
    min_h = 720 if target_w >= 1920 else 640
    out = []
    for page in (r.json().get("query", {}).get("pages") or {}).values():
        info = (page.get("videoinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not _commercial_ok(lic) or (info.get("height") or 0) < min_h:
            continue
        # prefer a VP9/MP4 transcode close to the size we need, else the original file
        derivs = sorted((d for d in info.get("derivatives", []) if (d.get("height") or 0) >= min_h
                         and d.get("type", "").startswith("video/")), key=lambda d: d.get("height") or 0)
        link = derivs[0]["src"] if derivs else info.get("url")
        if not link or (not derivs and (info.get("size") or 0) > 400 * 1024 * 1024):
            continue  # no link, or a huge original with no smaller copy
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "Unknown"
        title = page["title"].removeprefix("File:").rsplit(".", 1)[0]
        about = (title + " " + re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))).lower()
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        if not all(w in about for w in words) or "google" in artist.lower():
            continue  # off-topic search hit, or Google Earth imagery (own terms of use)
        pageurl = info.get("descriptionurl", "")
        out.append(("wikimedia", link, f"{artist} (Wikimedia Commons) - \"{title}\", {lic}, {pageurl}", pageurl))
        if len(out) >= n:
            break
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
        out.append(("youtube", url, yt_credit(sn["title"], sn["channelTitle"], url), url))
    return out


def _iso_seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mnt, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mnt * 60 + s


def top_cc_videos(query, n, min_minutes=3, max_minutes=25, log=print):
    """Most-viewed Creative Commons (CC BY) YouTube videos for `query`.

    The licence is checked twice: by the search filter and again per video (status.license).
    Returns dicts: id, url, title, channel, views, seconds.
    """
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        return []
    r = requests.get("https://www.googleapis.com/youtube/v3/search",
                     params={"part": "snippet", "q": query, "type": "video", "videoLicense": "creativeCommon",
                             "order": "viewCount", "maxResults": 25, "key": key}, headers=UA, timeout=30)
    r.raise_for_status()
    ids = [it["id"]["videoId"] for it in r.json().get("items", [])]
    if not ids:
        return []
    r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                     params={"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids), "key": key},
                     headers=UA, timeout=30)
    r.raise_for_status()
    out = []
    for v in r.json().get("items", []):
        st, sn = v.get("status", {}), v["snippet"]
        secs = _iso_seconds(v["contentDetails"].get("duration"))
        if st.get("license") != "creativeCommon" or st.get("privacyStatus") != "public":
            continue
        if not min_minutes * 60 <= secs <= max_minutes * 60:
            continue  # too short to cut from, or too long for Gemini to watch cheaply
        out.append({"id": v["id"], "url": f"https://www.youtube.com/watch?v={v['id']}",
                    "title": sn["title"], "channel": sn["channelTitle"],
                    "views": int(v.get("statistics", {}).get("viewCount", 0)), "seconds": float(secs)})
    out.sort(key=lambda x: x["views"], reverse=True)
    for x in out[:n]:
        log(f"    source: {x['title']} - {x['channel']} ({x['views']:,} views, {x['seconds'] / 60:.0f} min)")
    return out[:n]


def download_sources(sources, dest_dir: Path, log=print):
    """Download the plan's source videos. Returns {1-based index: (path, duration)}."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    got = {}
    for i, s in enumerate(sources, 1):
        try:
            p = _download_youtube(s.url, dest_dir)
        except Exception as e:
            log(f"    could not download {s.url}: {e}")
            continue
        if p:
            got[i] = (p, duration(p))
            log(f"    + downloaded source {i} ({got[i][1]:.0f}s)")
    return got


def segment_clips(spot, sources, downloaded) -> list[Clip]:
    """Turn a spot's Gemini timestamps into clips that point into the downloaded source videos."""
    clips = []
    for seg in spot.segments:
        if seg.video not in downloaded:
            continue
        path, d = downloaded[seg.video]
        start, end = max(0.0, seg.start), min(seg.end, d)
        if end - start < 1.5:
            continue
        s = sources[seg.video - 1]
        clips.append(Clip(path, d, "youtube", yt_credit(s.title, s.channel, s.url), s.url, start, end))
    return clips


def _download_youtube(url: str, dest_dir: Path) -> Path | None:
    import yt_dlp
    opts = {
        "format": "bv*[height<=1080][ext=mp4]/b[ext=mp4]/b",
        "outtmpl": str(dest_dir / "yt_%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "ffmpeg_location": FFMPEG,
        "match_filter": yt_dlp.utils.match_filter_func("duration < 1800"),
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
                   lambda: search_wikimedia(term, 3, target_w) if settings.get("use_wikimedia", True) else [],
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
            if d < 2:  # broken or unreadable file
                continue
            seen.add(page)
            clips.append(Clip(path, d, source, credit, page))
            log(f"    + {source}: {page} ({d:.0f}s)")
    return clips
