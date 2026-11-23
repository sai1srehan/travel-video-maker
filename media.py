"""Thin helpers around the ffmpeg binary bundled with imageio-ffmpeg."""
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg(args, cwd=None):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")


def duration(path: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, errors="ignore")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
