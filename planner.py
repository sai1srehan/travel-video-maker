"""Uses Gemini to pick the places and write the voiceover script.

Two modes:
  * make_plan_from_videos: Gemini watches the source YouTube videos (Creative Commons only),
    finds where each place appears (timestamps) and writes narration for what is on screen.
  * make_plan: no source videos - Gemini picks places, footage is searched per place.
"""
import os

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"


# ---- stored plan (plan.json) ----------------------------------------------------------

class Segment(BaseModel):
    video: int  # 1-based index into VideoPlan.sources
    start: float  # seconds
    end: float


class SourceVideo(BaseModel):
    id: str
    url: str
    title: str
    channel: str
    views: int = 0
    seconds: float = 0


class Spot(BaseModel):
    name: str
    search_terms: list[str]
    narration: str
    segments: list[Segment] = []


class VideoPlan(BaseModel):
    city: str
    country: str
    title: str
    intro_narration: str
    spots: list[Spot]
    outro_narration: str
    description: str
    tags: list[str]
    sources: list[SourceVideo] = []


# ---- what Gemini returns (no default values: the Gemini schema doesn't support them) ---

class _Segment(BaseModel):
    video: int = Field(description="Which source video this shot is from: 1 for the first video, 2 for the second, ...")
    start: float = Field(description="Start time in seconds from the beginning of that video")
    end: float = Field(description="End time in seconds; the place must be clearly on screen the whole time")


class _Spot(BaseModel):
    name: str = Field(description="Place name in English, short enough for an on-screen title")
    search_terms: list[str] = Field(
        description="2-3 short English phrases for searching stock footage of this exact place, "
                    "most specific first, e.g. ['Petronas Towers Kuala Lumpur', 'Petronas Towers night']")
    narration: str = Field(description="Voiceover for this place, 35-55 words, in the requested language")


class _VideoSpot(_Spot):
    segments: list[_Segment] = Field(
        description="2-4 shots of this place taken from the source videos, each 4-20 seconds long. "
                    "Use shots from different source videos when the place appears in more than one.")


class _Script(BaseModel):
    title: str = Field(description="Catchy YouTube title, under 70 characters")
    intro_narration: str = Field(description="Hook for the first ~8 seconds, 15-25 words")
    spots: list[_Spot]
    outro_narration: str = Field(description="Sign-off asking viewers to like/subscribe, 15-25 words")
    description: str = Field(description="YouTube description, 2-3 short paragraphs")
    tags: list[str] = Field(description="10-15 YouTube tags")


class _VideoScript(_Script):
    spots: list[_VideoSpot]


SYSTEM = """You write voiceover scripts for a weekly travel video channel.
Each episode covers one destination and its must-see places. The narration is read
by a text-to-speech voice over the footage, so:
- write for the ear: short sentences, no lists, no emojis, no stage directions;
- talk about what the viewer is seeing at that place;
- every fact (timings, fees, history) must be well known and stable; if unsure, leave it out;
- the narration is entirely your own words: never copy or translate the source videos' speech.
search_terms and spot names are always in English, even when the narration is not."""

VIDEO_RULES = """You are given {n} source videos (Creative Commons, reuse allowed) about this destination.
Watch them and build the episode ONLY from places that are clearly shown in them:
- pick up to {k} places, in a sensible route order, and give each one exact shot timestamps;
- skip intros, outros, talking heads, title cards, subscribe screens, text overlays and logos;
- prefer wide, steady, beautiful shots; never use a shot that shows a different place;
- the narration for each place must match what those shots show."""


def _prompt(city, country, settings):
    return (f"Destination this week: {city}, {country}.\n"
            f"Narration language: {settings['language']}. The YouTube title and description should "
            f"also be in {settings['language']}, with the place names in English.\n"
            f"Host personality: {settings['host_style']}.")


def _ask(contents, schema, system):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=MODEL, contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,  # keeps long videos cheap
        ),
    )
    return response.parsed if response.parsed is not None else schema.model_validate_json(response.text)


def make_plan(city: str, country: str, settings: dict) -> VideoPlan:
    prompt = (_prompt(city, country, settings) +
              f"\nPick the {settings['places_per_video']} best places to visit there, in a sensible route order.")
    script = _ask(prompt, _Script, SYSTEM)
    return VideoPlan(city=city, country=country, **script.model_dump())


def make_plan_from_videos(city: str, country: str, sources: list[SourceVideo], settings: dict) -> VideoPlan:
    parts = []
    for i, s in enumerate(sources, 1):
        parts.append(types.Part(text=f"Source video {i}: \"{s.title}\" by {s.channel}"))
        parts.append(types.Part(file_data=types.FileData(file_uri=s.url, mime_type="video/*")))
    parts.append(types.Part(text=_prompt(city, country, settings)))
    system = SYSTEM + "\n\n" + VIDEO_RULES.format(n=len(sources), k=settings["places_per_video"])
    script = _ask(parts, _VideoScript, system)

    plan = VideoPlan(city=city, country=country, sources=sources, **script.model_dump())
    for spot in plan.spots:  # drop timestamps that point outside the videos
        good = []
        for seg in spot.segments:
            if not 1 <= seg.video <= len(sources):
                continue
            src = sources[seg.video - 1]
            start, end = max(0.0, seg.start), min(seg.end, src.seconds or seg.end)
            if end - start >= 1.5:
                good.append(Segment(video=seg.video, start=start, end=end))
        spot.segments = good
    return plan
