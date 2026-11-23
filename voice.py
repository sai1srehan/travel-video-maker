"""Voiceover. Two free engines:

  * gemini - Gemini speech generation: natural, expressive, follows a style prompt
             ("cheerful travel vlogger"). Uses your GEMINI_API_KEY (free tier has a small daily limit).
  * edge   - Microsoft Edge neural voices, no key. Used automatically if Gemini fails.
             Telugu: te-IN-MohanNeural, te-IN-ShrutiNeural.  List all: edge-tts --list-voices

Gemini voices (any language): Puck (upbeat), Charon (informative), Fenrir (excitable), Orus (firm),
Kore (firm, female), Aoede (breezy, female), Leda (youthful, female), Zephyr (bright, female) ...
"""
import asyncio
import os
import time
import wave
from pathlib import Path

LANG_CODES = {"telugu": "te-IN", "hindi": "hi-IN", "tamil": "ta-IN", "kannada": "kn-IN",
              "malayalam": "ml-IN", "marathi": "mr-IN", "bengali": "bn-IN", "english": "en-IN"}


def _edge(text, out: Path, voice, rate):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out)))
    return out


def _gemini(text, out: Path, settings):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    style = settings.get("voice_style", "Say this like a cheerful, friendly travel vlogger talking to friends")
    lang = LANG_CODES.get(settings.get("language", "").lower())
    resp = client.models.generate_content(
        model=settings.get("tts_model", "gemini-2.5-flash-preview-tts"),
        contents=f"{style}:\n{text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                language_code=lang,
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.get("gemini_voice", "Puck")))),
        ),
    )
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    if not pcm:
        raise RuntimeError("Gemini returned no audio")
    with wave.open(str(out), "wb") as w:  # Gemini returns raw 16-bit 24 kHz mono PCM
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    return out


_gemini_broken = False  # after Gemini fails once, the rest of the video uses Edge (one consistent voice)


def speak(text: str, out_base: Path, settings: dict, log=print) -> Path:
    """Writes out_base.wav (Gemini) or out_base.mp3 (Edge). Returns the file written."""
    global _gemini_broken
    if settings.get("tts_engine", "gemini") == "gemini" and not _gemini_broken:
        err = None
        for wait in (0, 15, 45):  # free tier: a few requests per minute
            time.sleep(wait)
            try:
                return _gemini(text, out_base.with_suffix(".wav"), settings)
            except Exception as e:  # rate limit / quota / model renamed
                err = e
        _gemini_broken = True
        log(f"    ! Gemini voice failed ({err}) - using the Edge voice")
    return _edge(text, out_base.with_suffix(".mp3"), settings["voice"], settings.get("voice_rate", "+0%"))
