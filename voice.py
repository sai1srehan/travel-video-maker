"""Text-to-speech with Microsoft Edge neural voices (free, no key).

Some good voices:  en-IN-PrabhatNeural, en-IN-NeerjaNeural (Indian English)
                   hi-IN-MadhurNeural, hi-IN-SwaraNeural  (Hindi)
                   te-IN-MohanNeural,  te-IN-ShrutiNeural (Telugu)
                   ta-IN-ValluvarNeural (Tamil), en-US-AndrewNeural (US English)
List all with:  edge-tts --list-voices
"""
import asyncio
from pathlib import Path

import edge_tts


async def _say(text, voice, rate, out):
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def speak(text: str, out: Path, voice: str, rate: str = "+0%") -> Path:
    asyncio.run(_say(text, voice, rate, out))
    return out
