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
import time
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

    @property
    def is_photo(self):
        return Path(self.path).suffix.lower() in PHOTO_EXTS


PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
PHOTO_SECONDS = 6.0  # a photo is shown once, for one shot, with a slow zoom or pan


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def matches(text: str, keywords: list[str]) -> bool:
    """True if the photo/video's tags or title name the place (any keyword, whole words)."""
    t = _norm(text)
    return any(_norm(k) in t for k in keywords if k.strip())


GENERIC = set("""the of and a in at fort palace temple lake beach market bazaar street road garden gardens park
museum city old new view point viewpoint hill hills falls waterfall river island national tower towers gate
square mosque church cathedral station bridge food night day trip sunset sunrise area village district center
centre mall india""".split())


def place_keywords(spot, city: str) -> list[str]:
    """Words that must appear in a clip's tags/title for it to count as showing this place."""
    kws = [k for k in getattr(spot, "keywords", None) or [] if k.strip()]
    if kws:
        return kws
    words = [w for w in re.findall(r"[a-z0-9]+", spot.name.lower()) if w not in GENERIC and w not in _norm(city)]
    return [spot.name] + ([" ".join(words)] if words else [])


def yt_credit(title, channel, url):
    return f"\"{title}\" by {channel} - CC BY, {url}"


def _download(url: str, dest_dir: Path, prefix: str, ext: str = ".mp4") -> Path:
    name = prefix + "_" + hashlib.md5(url.encode()).hexdigest()[:12] + ext
    path = dest_dir / name
    if path.exists() and path.stat().st_size > 0:
        return path
    started, size = time.monotonic(), 0
    tmp = path.with_suffix(".part")
    with requests.get(url, stream=True, timeout=(15, 30), headers=UA) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                size += len(chunk)
                # a slow server or a huge file can stall the whole run: give up and use other footage
                if time.monotonic() - started > MAX_DOWNLOAD_SECS or size > MAX_DOWNLOAD_BYTES:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"too slow/too big ({size >> 20} MB in {time.monotonic() - started:.0f}s)")
    tmp.replace(path)
    return path


MAX_DOWNLOAD_SECS = 120
MAX_DOWNLOAD_BYTES = 200 << 20


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
        if f:  # the page URL slug is the video's title, e.g. /video/amber-fort-jaipur-12345/
            out.append(_hit("pexels", f["link"], f"{v['user']['name']} (Pexels)", v["url"], v["url"]))
    return out


def _hit(source, link, credit, page, text, photo=False):
    return {"source": source, "link": link, "credit": credit, "page": page, "text": text, "photo": photo}


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
            out.append(_hit("pixabay", f["link"], f"{h['user']} (Pixabay)", h["pageURL"],
                            f"{h.get('tags', '')} {h['pageURL']}"))
    return out


def search_pixabay_photos(query, n, orientation):
    """Pixabay photos (free for commercial use, no attribution needed) - landmarks have many more
    photos than videos, so these cover places with little video footage."""
    key = os.getenv("PIXABAY_API_KEY")
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/", headers=UA, timeout=30, params={
        "key": key, "q": query[:100], "image_type": "photo", "per_page": max(3, min(200, n)),
        "orientation": "vertical" if orientation == "portrait" else "horizontal",
        "min_width": 1280, "safesearch": "true"})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", [])[:n]:
        link = h.get("largeImageURL") or h.get("webformatURL")
        if link:
            out.append(_hit("pixabay", link, f"{h['user']} (Pixabay)", h["pageURL"],
                            f"{h.get('tags', '')} {h['pageURL']}", photo=True))
    return out


_NOT_PHOTOS = ("map", "plan", "logo", "diagram", "coat of arms", "stamp", "flag", "drawing", "painting",
               "sketch", "ticket", "sign", "chart", "svg")


def search_wikimedia_photos(query, n, target_w):
    """CC0 / public domain / CC BY photos from Wikimedia Commons (share-alike ones are skipped)."""
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": 50,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": max(1920, target_w)})
    r.raise_for_status()
    out = []
    for page in (r.json().get("query", {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not _commercial_ok(lic) or (info.get("width") or 0) < 1600:
            continue
        title = page["title"].removeprefix("File:").rsplit(".", 1)[0]
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "Unknown"
        if any(x in title.lower() for x in _NOT_PHOTOS) or "google" in artist.lower():
            continue
        about = title + " " + re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))
        pageurl = info.get("descriptionurl", "")
        link = info.get("thumburl") or info.get("url")
        out.append(_hit("wikimedia", link, f"{artist} (Wikimedia Commons) - \"{title}\", {lic}, {pageurl}",
                        pageurl, about, photo=True))
        if len(out) >= n:
            break
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
        out.append(_hit("wikimedia", link, f"{artist} (Wikimedia Commons) - \"{title}\", {lic}, {pageurl}",
                        pageurl, about))
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
        out.append(_hit("youtube", url, yt_credit(sn["title"], sn["channelTitle"], url), url,
                        f"{sn['title']} {sn.get('description', '')}"))
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


def _search(term, settings, photos: bool, per: int, target_w: int, log) -> list[dict]:
    o = settings["orientation"]
    if photos:
        fns = [lambda: search_pixabay_photos(term, per, o),
               lambda: search_wikimedia_photos(term, per, target_w) if settings.get("use_wikimedia", True) else []]
    else:
        fns = [lambda: search_pexels(term, per, o, target_w),
               lambda: search_pixabay(term, per, target_w),
               lambda: search_wikimedia(term, 3, target_w) if settings.get("use_wikimedia", True) else [],
               lambda: search_youtube_cc(term, 2, settings["youtube_days"]) if settings.get("use_youtube_cc") else []]
    hits = []
    for fn in fns:
        try:
            hits += fn()
        except requests.RequestException as e:
            log(f"    search error for '{term}': {e}")
    return hits


def gather(terms, settings, dest_dir: Path, seen: set, log=print, keywords=None, shots=None) -> list[Clip]:
    """Footage that really shows the place: every hit's tags/title must contain one of `keywords`.

    Videos first (up to clips_per_place), then photos until roughly `shots` shots are covered.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    want_videos = settings["clips_per_place"]
    shots = shots or want_videos
    vertical = settings["orientation"] == "portrait"
    target_w = 1080 if vertical else 1920
    cut = float(settings.get("cut_seconds", 3.5))
    clips: list[Clip] = []
    rejected = 0

    def covered():  # rough number of shots the footage so far can give without repeats
        return sum(1 if c.is_photo else max(1, int(c.duration * 0.84 // (cut * 1.1))) for c in clips)

    for photos in (False, True):
        for term in terms:
            if (not photos and len(clips) >= want_videos) or covered() >= shots:
                break
            per = 20 if photos else max(4, min(10, want_videos - len(clips)))
            for hit in _search(term, settings, photos, per, target_w, log):
                if (not photos and len(clips) >= want_videos) or covered() >= shots:
                    break
                if hit["page"] in seen:
                    continue
                if keywords and not matches(hit["text"], keywords):
                    rejected += 1  # e.g. a lake when we asked for a fort: never show the wrong place
                    continue
                src = hit["source"]
                try:
                    if src == "youtube":
                        path = _download_youtube(hit["link"], dest_dir)
                    else:
                        path = _download(hit["link"], dest_dir, src, ".jpg" if hit["photo"] else ".mp4")
                except Exception as e:  # network / extractor errors: skip this one
                    seen.add(hit["page"])  # don't retry it for the next search term
                    log(f"    skip {hit['page']}: {str(e)[:120]}")
                    continue
                if not path:
                    continue
                if hit["photo"]:
                    if path.stat().st_size < 30_000:  # broken / tiny image
                        continue
                    clip = Clip(path, PHOTO_SECONDS, src, hit["credit"], hit["page"], 0.0, PHOTO_SECONDS)
                else:
                    d = duration(path)
                    if d < 2:  # broken or unreadable file
                        continue
                    clip = Clip(path, d, src, hit["credit"], hit["page"])
                seen.add(hit["page"])
                clips.append(clip)
                log(f"    + {src} {'photo' if hit['photo'] else 'video'}: {hit['page']}")
    if rejected:
        log(f"    ({rejected} search results skipped: their tags don't mention this place)")
    return clips
