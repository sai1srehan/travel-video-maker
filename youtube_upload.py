"""Uploads the finished video + thumbnail to your YouTube channel (YouTube Data API v3).

One-time setup (on your PC) - see README "Auto-upload to YouTube":
  1. Put your OAuth "Desktop app" client file here as client_secret.json
  2. python youtube_upload.py --login      (opens the browser, saves token.json)
  3. For GitHub Actions: copy the whole content of token.json into the secret YOUTUBE_TOKEN_JSON
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIENT = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
LANGS = {"telugu": "te", "hindi": "hi", "tamil": "ta", "kannada": "kn", "malayalam": "ml",
         "marathi": "mr", "bengali": "bn", "english": "en"}


def login():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENT.exists():
        sys.exit(f"Download your OAuth client (Desktop app) from Google Cloud and save it as {CLIENT}")
    creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(port=0)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved {TOKEN}\nFor GitHub: paste the whole content of token.json into the secret YOUTUBE_TOKEN_JSON")


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    raw = os.getenv("YOUTUBE_TOKEN_JSON") or (TOKEN.read_text(encoding="utf-8") if TOKEN.exists() else "")
    if not raw.strip():
        raise RuntimeError("Not logged in to YouTube: run  python youtube_upload.py --login  "
                           "(or set the YOUTUBE_TOKEN_JSON secret on GitHub)")
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    return creds


def _clean(text, limit):
    return text.replace("<", "").replace(">", "")[:limit]  # YouTube rejects < and >


def _fit_tags(tags, limit=480):
    out, used = [], 0
    for t in (_clean(t, 100).strip() for t in tags):
        cost = len(t) + (2 if " " in t else 0) + 1
        if t and used + cost <= limit:
            out.append(t)
            used += cost
    return out


def upload(video: Path, thumbnail: Path | None, title: str, description: str, tags: list[str],
           settings: dict, log=print) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    up = settings.get("upload", {})
    yt = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    lang = LANGS.get(settings.get("language", "").lower())
    snippet = {"title": _clean(title, 100), "description": _clean(description, 5000),
               "tags": _fit_tags(tags), "categoryId": str(up.get("category_id", "19"))}
    if lang:  # tells YouTube who to recommend it to (Telugu viewers)
        snippet.update(defaultLanguage=lang, defaultAudioLanguage=lang)
    body = {"snippet": snippet,
            "status": {"privacyStatus": up.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                       "embeddable": True, "license": "youtube"}}
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"  uploading... {int(status.progress() * 100)}%")
    vid = response["id"]
    wanted = body["status"]["privacyStatus"]
    got = (response.get("status") or {}).get("privacyStatus", wanted)
    log(f"  uploaded: https://youtu.be/{vid} ({got})")
    if got != wanted:
        log(f"  ! YouTube made this video {got.upper()} although we asked for {wanted}. This happens to every "
            f"upload from a Google Cloud project that hasn't passed YouTube's API audit. Until then, open "
            f"YouTube Studio -> Content and switch it to Public by hand. Apply (free) here: "
            f"https://support.google.com/youtube/contact/yt_api_form")

    if thumbnail and Path(thumbnail).exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
            log("  thumbnail set")
        except HttpError as e:
            log(f"  ! thumbnail not set - verify your channel at https://www.youtube.com/verify ({e.status_code})")
    return f"https://youtu.be/{vid}"


if __name__ == "__main__":
    if "--login" in sys.argv:
        login()
    else:
        print(__doc__)
