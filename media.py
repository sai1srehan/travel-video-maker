"""Thin helpers around the ffmpeg binary bundled with imageio-ffmpeg."""
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg(args, cwd=None, timeout=2400):
    """timeout: no single ffmpeg step should take 40 min; if one hangs, fail loudly instead of
    silently eating the whole GitHub Actions time limit."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="ignore", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg took over {timeout}s and was stopped:\n{' '.join(cmd)[:1500]}")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")


def duration(path: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, errors="ignore", timeout=60)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
