# Weekly Travel Video Maker

Every week this picks one destination (India or worldwide). It asks Gemini for the best
places to see there and a voiceover script, then collects footage of each place. It edits
the footage into quick graded cuts with titles, records an AI voiceover, and produces:

- `video.mp4`: the finished video (16:9, or 9:16 with `--portrait`)
- `thumbnail.jpg`
- `upload.txt`: title, description (with footage credits) and tags, ready to paste into YouTube

## Where the footage comes from (and why)

Downloading other creators' normal YouTube videos and re-uploading them, even trimmed and
re-voiced, is copyright infringement. It leads to Content ID claims, strikes and
demonetisation. This app only uses footage that you're allowed to reuse:

| Source | Licence | Credit needed |
|---|---|---|
| Pexels | Free, commercial use OK | No (credited anyway) |
| Pixabay | Free, commercial use OK | No (credited anyway) |
| YouTube Creative Commons | CC BY, reuse allowed | **Yes**: added automatically on screen and in the description |

For YouTube it searches this week's CC uploads first, then CC videos from any date.

## Run on GitHub (weekly, automatic)

1. Create a new GitHub repo and push this folder to it:
   ```
   git init && git add . && git commit -m "Travel video maker"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. In the repo go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `GEMINI_API_KEY`: https://aistudio.google.com/apikey
   - `PEXELS_API_KEY`: https://www.pexels.com/api/
   - `PIXABAY_API_KEY`: https://pixabay.com/api/docs/
   - `YOUTUBE_API_KEY` (optional): YouTube Data API v3 key from Google Cloud Console
   - Optional variable (Variables tab) `GEMINI_MODEL` to use a different Gemini model (default `gemini-2.5-flash`)
3. Go to **Actions → Weekly travel video → Run workflow** to test it now. You can type a city or leave it empty.
4. When the run finishes, open it and download the **travel-video-N** artifact (it's kept for 30 days).

It then runs by itself every Sunday at 09:00 IST and moves to the next city in
`destinations.json`. Used cities are tracked in `output/history.json`, which the workflow commits back.

> Note: YouTube often blocks downloads from GitHub's servers ("confirm you're not a bot").
> When that happens those clips are skipped and Pexels/Pixabay footage is used instead.
> For the most YouTube CC footage, run it on your own PC.

## Run on your PC

```
pip install -r requirements.txt
copy .env.example .env      # then fill in your keys
python main.py                         # this week's destination
python main.py --city Hampi --country India
python main.py --portrait              # Shorts / Reels
python main.py --plan-only             # only write the script
```

To change the script, edit `output/<week>_<city>/plan.json` and run again. It reuses your edited plan.

## Make it your own style: `settings.json`

| Setting | What it does |
|---|---|
| `language` | Narration language (e.g. `Hindi`, `Telugu`) |
| `voice` | TTS voice. `en-IN-PrabhatNeural`, `hi-IN-MadhurNeural`, `te-IN-MohanNeural`, ... (`edge-tts --list-voices`) |
| `voice_rate` | Speaking speed, e.g. `+10%` |
| `host_style` | The personality Gemini writes in |
| `places_per_video`, `clips_per_place` | Video length / variety |
| `cut_seconds` | Length of each shot (shorter = faster pace) |
| `color_grade` | ffmpeg colour filter applied to every shot |
| `music_file`, `music_volume` | Background music. Put a royalty-free track (e.g. from the YouTube Audio Library) at `assets/music.mp3` |
| `use_youtube_cc`, `youtube_days` | Creative Commons YouTube search |

Add or reorder cities in `destinations.json`.
