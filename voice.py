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
import wave
from pathlib import Path

LANG_CODES = {"telugu": "te-IN", "hindi": "hi-IN", "tamil": "ta-IN", "kannada": "kn-IN",
              "malayalam": "ml-IN", "marathi": "mr-IN", "bengali": "bn-IN", "english": "en-IN"}


def _edge(text, out: Path, voice, rate):
    import edge_tts
    last = None
    for attempt in range(3):
        try:  # the Edge service occasionally stalls; don't let it hang the run
            asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(str(out)), 180))
            return out
        except Exception as e:
            last = e
    raise RuntimeError(f"Edge voice failed 3 times: {last!r}")


def _gemini(text, out: Path, settings):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(timeout=180_000))  # ms; a stuck request must not hang the run
    style = settings.get("voice_style", "Say this like a cheerful, friendly travel vlogger talking to friends")
    lang = LANG_CODES.get(settings.get("language", "").lower())
    import gemini_models
    resp = gemini_models.generate(
        client, settings.get("tts_model"), "tts", waits=(20, 60),
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
        try:  # retries + fallback models happen inside gemini_models.generate
            return _gemini(text, out_base.with_suffix(".wav"), settings)
        except Exception as e:  # quota used up / no TTS model for this key
            _gemini_broken = True
            log(f"    ! Gemini voice failed ({str(e)[:150]}) - using the Edge voice")
    return _edge(text, out_base.with_suffix(".mp3"), settings["voice"], settings.get("voice_rate", "+0%"))
