# Weekly Travel Video Maker

Every week this picks one destination (or one you choose, e.g. `--city Malaysia`) and:

1. Searches YouTube for the **most-viewed Creative Commons (CC BY) videos** of that place and takes the top 2.
2. Gemini **watches both videos**, picks the best places shown in them (Petronas Towers, Batu Caves, ...),
   notes the exact timestamps where each place is on screen, and writes a **Telugu** voiceover for those shots.
3. The editor cuts between both videos place by place, with quick graded shots, titles and a fade between sections.
   Places with too few shots are filled with Pexels/Pixabay stock footage.
4. An AI voice (Microsoft Edge neural TTS) reads the narration, background music is mixed in.
5. The video, thumbnail, title, description (with the required CC credits) and tags are **uploaded to your channel**.

Output per video in `output/<week>_<city>/`: `video.mp4`, `thumbnail.jpg`, `upload.txt`, `plan.json`, `youtube_url.txt`.

## Why only Creative Commons videos

Re-uploading other creators' normal YouTube videos, even trimmed, mixed and re-voiced, gets Content ID
claims and copyright strikes, and channels built from them fail YouTube's "reused content" monetization review.
CC BY videos are licensed for reuse as long as you credit them, which this app does automatically
(on screen and in the description). Every video's licence is checked twice (search filter + per-video `status.license`).
Your own narration, place selection and editing are what make the result original.

| Source | Licence | Credit needed |
|---|---|---|
| YouTube Creative Commons | CC BY 3.0 | **Yes**: added automatically |
| Pexels / Pixabay | Free, commercial use OK | No (credited anyway) |

## Keys you need

| Key | Needed for | Get it |
|---|---|---|
| `GEMINI_API_KEY` | script + watching the videos | https://aistudio.google.com/apikey |
| `YOUTUBE_API_KEY` | finding CC videos | Google Cloud → enable **YouTube Data API v3** → Credentials → API key |
| `PEXELS_API_KEY` | filling gaps (optional) | https://www.pexels.com/api/ |
| `PIXABAY_API_KEY` | filling gaps (optional) | https://pixabay.com/api/docs/ |
| `token.json` / `YOUTUBE_TOKEN_JSON` | uploading to your channel | see below |

## Auto-upload to YouTube (one-time setup, on your PC)

1. https://console.cloud.google.com → create a project → **APIs & Services → Library** → enable **YouTube Data API v3**
   (the same project can hold your `YOUTUBE_API_KEY`).
2. **OAuth consent screen** → External → fill in app name + your email → add yourself under **Test users**.
   Then click **Publish app** (status "In production"). In "Testing" status the login expires every 7 days,
   which breaks the weekly run. You don't need Google's verification for your own channel.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON and save it in this
   folder as `client_secret.json`.
4. Run `python youtube_upload.py --login`. A browser opens: pick your channel's Google account. If it says
   "Google hasn't verified this app", click **Advanced → Go to ... (unsafe)**; it's your own app.
   This saves `token.json`.
5. For GitHub: open `token.json`, copy everything, and save it as the repo secret `YOUTUBE_TOKEN_JSON`.

`client_secret.json` and `token.json` are in `.gitignore`. Never commit them.

**Videos upload as Private.** YouTube locks API uploads from new, unaudited Google Cloud projects to private.
Check the video in YouTube Studio and switch it to Public yourself (good practice anyway). To publish
automatically, pass YouTube's API audit (https://support.google.com/youtube/contact/yt_api_form) and then set
`"privacy": "public"` in `settings.json`. To set custom thumbnails, verify your channel at https://www.youtube.com/verify.

## Run on GitHub (weekly, automatic)

1. Push this folder to a GitHub repo.
2. **Settings → Secrets and variables → Actions → New repository secret**: `GEMINI_API_KEY`, `YOUTUBE_API_KEY`,
   `YOUTUBE_TOKEN_JSON`, and optionally `PEXELS_API_KEY`, `PIXABAY_API_KEY`.
3. **Settings → Actions → General → Workflow permissions → Read and write** (so it can save `output/history.json`).
4. **Actions → Weekly travel video → Run workflow** to test. Type a city (e.g. `Malaysia`) or leave it empty.

It then runs every Sunday at 09:00 IST and moves to the next place in `destinations.json`.

> Note: YouTube often blocks video downloads from GitHub's servers ("confirm you're not a bot").
> Gemini can still watch the videos and write the script, but the shots then come from Pexels/Pixabay.
> For videos that really use the YouTube footage, run it on your own PC.

## Run on your PC

```
pip install -r requirements.txt
winget install DenoLand.Deno          # yt-dlp needs it to download from YouTube
copy .env.example .env                # then fill in your keys
python youtube_upload.py --login      # once, for auto-upload
python main.py --city Malaysia
python main.py                        # this week's destination
python main.py --portrait             # Shorts / Reels
python main.py --plan-only            # only write the script
python main.py --no-upload            # make the video, don't upload
```

To change the script, edit `output/<week>_<city>/plan.json` and run again. It reuses your edited plan.

## Settings: `settings.json`

| Setting | What it does |
|---|---|
| `language`, `voice`, `voice_rate` | Narration language and TTS voice: `te-IN-MohanNeural` / `te-IN-ShrutiNeural` (Telugu), `hi-IN-MadhurNeural`, `en-IN-PrabhatNeural`, ... (`edge-tts --list-voices`) |
| `host_style` | The personality Gemini writes in |
| `footage_mode` | `top_cc_videos` (most-viewed CC videos, mixed) or `stock` (search footage per place) |
| `source_videos` | How many top CC videos to mix (default 2) |
| `fill_with_stock` | Use Pexels/Pixabay for places with too few shots |
| `places_per_video`, `clips_per_place`, `cut_seconds` | Length and pace |
| `color_grade` | ffmpeg colour filter applied to every shot |
| `music_file`, `music_volume` | Background music: put a royalty-free track at `assets/music.mp3` |
| `upload.enabled`, `upload.privacy`, `upload.category_id` | Auto-upload; `19` = Travel & Events |
