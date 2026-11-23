# Weekly Telugu Travel Video Maker

Runs on GitHub every Sunday, no PC needed. It's completely free (all services used have free tiers).
Every week it uploads a **~20-minute video** (set 12-30 in `settings.json`) plus a **2-minute vertical Short**
that teases it and links to it. For each week's destination it:

1. **Script:** Gemini picks the best places and writes a Telugu voiceover that sounds like a
   real Telugu travel YouTuber: casual Telugu with everyday English words, tips, budget, humour, no AI clichés.
2. **Footage:** downloads shots of each place from **Pexels**, **Pixabay** and **Wikimedia Commons**,
   all free and safe for monetization. From Wikimedia only CC0, public domain and CC BY are used.
3. **Voice:** Gemini's speech voice (natural, expressive Telugu); Edge TTS is the automatic backup.
4. **Editing:** hand-edit style: shot lengths vary, mostly straight cuts with some slow camera drift,
   colour grade, place-name titles, background music (a different track each week), credits.
5. **Upload:** video, thumbnail, title, description (with all required credits) and tags go to your channel.

Each run's files are also saved as a GitHub artifact: `video.mp4`, `thumbnail.jpg`, `upload.txt`, `plan.json`.

## Setup (all in the browser, once)

### 1. Keys (free)

| Secret | Get it |
|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ |
| `YOUTUBE_TOKEN_JSON` | step 2 |
| `PEXELS_API_KEY` (optional) | https://www.pexels.com/api/ (new keys are paused right now; add it later if they reopen) |

Wikimedia Commons needs no key.

### 2. YouTube upload login

1. https://console.cloud.google.com → create a project → **APIs & Services → Library** → enable **YouTube Data API v3**.
2. **OAuth consent screen** → External → app name + your email → add yourself under **Test users** →
   click **Publish app** (otherwise the login expires every 7 days). No Google verification needed for your own channel.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON.
4. Create `token.json` once by running `python youtube_upload.py --login` with that file saved as
   `client_secret.json` next to it. This one step needs a computer with Python, any computer for a minute,
   or ask a friend. Then paste the whole content of `token.json` into the GitHub secret `YOUTUBE_TOKEN_JSON`.

Never commit `client_secret.json` or `token.json` (they're in `.gitignore`).

### 3. GitHub

1. Push this folder to your repo.
2. **Settings → Secrets and variables → Actions**: add the secrets above.
3. **Settings → Actions → General → Workflow permissions → Read and write**.
4. **Actions → Weekly travel video → Run workflow** to test. Type a place (e.g. `Malaysia`) or leave it empty.
   Tip: for the first test, type `5` in **minutes** so it finishes quickly. A 20-minute video takes about 1-2 hours on GitHub.

### 4. Music (recommended)

Download a few royalty-free tracks from the YouTube Audio Library (YouTube Studio → Audio Library,
pick ones marked "no attribution required") and upload them to `assets/music/` in your repo
(GitHub → Add file → Upload files). A different track is picked each week.

## Good to know

- **Uploads are Private.** YouTube keeps API uploads from new Google Cloud projects private until the project
  passes YouTube's API audit (https://support.google.com/youtube/contact/yt_api_form). Until then, open
  YouTube Studio and click Public. After the audit, set `"privacy": "public"` in `settings.json`.
- **Custom thumbnails** need a verified channel: https://www.youtube.com/verify
- **Monetization:** YouTube's "inauthentic content" policy targets channels that mass-produce near-identical
  videos. What helps most: review each video before making it public, edit `plan.json` when something's off,
  and add your own touch (your face in the thumbnail, your own voice for the intro) when you can.
- **Free tier limits:** Gemini's free speech quota is small. If it runs out, that week's video uses the Edge voice.

## Change the style: `settings.json`

| Setting | What it does |
|---|---|
| `language`, `host_style` | Narration language and the host's personality |
| `tts_engine` | `gemini` (natural) or `edge` |
| `gemini_voice`, `voice_style` | Gemini voice (Puck, Charon, Fenrir, Kore, Aoede, Leda ...) and how it should sound |
| `voice`, `voice_rate` | Backup Edge voice: `te-IN-MohanNeural` or `te-IN-ShrutiNeural` |
| `target_minutes` | Long video length (12-30). Number of places, narration and footage scale with it |
| `words_per_second` | Speaking speed used to size the script; raise it if videos come out longer than wanted |
| `shorts.enabled`, `shorts.seconds`, `shorts.places` | The weekly vertical Short (max 180 s) |
| `cut_seconds`, `camera_move_share` | Average shot length; share of shots with slow camera drift (0-1) |
| `color_grade` | ffmpeg colour filter applied to every shot |
| `use_wikimedia` | Use Wikimedia Commons footage |
| `upload.enabled`, `upload.privacy` | Auto-upload on/off; `private` / `unlisted` / `public` |

Add or reorder destinations in `destinations.json`. To change one week's script, edit
`output/<week>_<city>/plan.json` locally and run again (`python main.py --city <place>`).
