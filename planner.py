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

import gemini_models

MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"  # auto-replaced if not available to your key


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


class ShortBeat(BaseModel):
    spot: int  # 1-based index into VideoPlan.spots
    narration: str


class VideoPlan(BaseModel):
    city: str
    country: str
    title: str
    intro_narration: str
    spots: list[Spot]
    outro_narration: str
    description: str
    tags: list[str]
    broll_terms: list[str] = []
    short_title: str = ""
    short_hook: str = ""
    short_beats: list[ShortBeat] = []
    short_outro: str = ""
    sources: list[SourceVideo] = []


def layout(settings: dict) -> dict:
    """Turn target_minutes into: number of places, seconds/words of narration per place, clips needed."""
    mins = float(settings.get("target_minutes", 20))
    wps = float(settings.get("words_per_second", 1.8))  # spoken Telugu, roughly
    places = int(settings.get("places_per_video") or 0) or max(5, min(18, round(mins * 0.65)))
    place_secs = max(20.0, (mins * 60 - 45) / places)  # 45 s for intro + outro
    sh = settings.get("shorts", {})
    short_secs, short_places = float(sh.get("seconds", 120)), int(sh.get("places", 4))
    beat_secs = max(8.0, (short_secs * 0.92 - 10) / short_places)  # hook + outro ~10 s, 8% safety margin
    return {"minutes": mins, "places": places, "place_secs": place_secs, "place_words": int(place_secs * wps),
            "clips_per_place": max(4, min(16, round(place_secs / 9))),
            "short_secs": short_secs, "short_places": short_places, "beat_words": int(beat_secs * wps)}


# ---- what Gemini returns (no default values: the Gemini schema doesn't support them) ---

class _Segment(BaseModel):
    video: int = Field(description="Which source video this shot is from: 1 for the first video, 2 for the second, ...")
    start: float = Field(description="Start time in seconds from the beginning of that video")
    end: float = Field(description="End time in seconds; the place must be clearly on screen the whole time")


class _Spot(BaseModel):
    name: str = Field(description="Place name in English, short enough for an on-screen title")
    search_terms: list[str] = Field(
        description="3-8 short English phrases for searching stock footage of this place, most specific first, "
                    "covering different views and things nearby so there is varied footage, e.g. "
                    "['Petronas Towers Kuala Lumpur', 'Petronas Towers night', 'KLCC park fountain', 'Kuala Lumpur skyline']")
    narration: str = Field(description="Voiceover for this place in the requested language, as long as the prompt asks")


class _ShortBeat(BaseModel):
    spot: int = Field(description="Which place this beat shows: 1 for the first place in spots, 2 for the second, ...")
    narration: str = Field(description="Voiceover for this beat in the requested language, as long as the prompt asks")


class _VideoSpot(_Spot):
    segments: list[_Segment] = Field(
        description="2-4 shots of this place taken from the source videos, each 4-20 seconds long. "
                    "Use shots from different source videos when the place appears in more than one.")


class _Script(BaseModel):
    title: str = Field(description="YouTube title in the style of popular Telugu travel channels: "
                                   "place name + a curiosity hook or budget/tip angle, under 70 characters, "
                                   "no clickbait lies")
    intro_narration: str = Field(description="Hook for the first ~8 seconds, 15-25 words: start mid-thought "
                                             "with something surprising about the place, not 'welcome to my channel'")
    spots: list[_Spot]
    outro_narration: str = Field(description="Sign-off asking viewers to like/subscribe, 15-25 words")
    description: str = Field(description="YouTube description, 2-3 short paragraphs")
    tags: list[str] = Field(description="10-15 YouTube tags")
    broll_terms: list[str] = Field(
        description="8-12 short English search phrases for general footage of the destination, no single landmark: "
                    "streets, local food, markets, transport, skyline, sunset, nature, people, e.g. "
                    "['Kuala Lumpur street food', 'Kuala Lumpur traffic night', 'Malaysia rainforest']")
    short_title: str = Field(description="YouTube Shorts title in the requested language, under 60 characters")
    short_hook: str = Field(description="First 3 seconds of the Short, 6-12 words: a surprising fact or question")
    short_beats: list[_ShortBeat] = Field(description="The Short's beats, one per place, best places only")
    short_outro: str = Field(description="6-12 words: the full video about this destination is on the channel")


class _VideoScript(_Script):
    spots: list[_VideoSpot]


SYSTEM = """You write voiceover scripts for a weekly travel YouTube channel, in the voice of its host.
Each episode covers one destination and its must-see places. The narration is spoken over the footage.

Sound like a real travel YouTuber talking to friends, not like a documentary or an AI:
- casual spoken language as people really talk; for Telugu, natural everyday Telugu with the English
  words Telugu people actually use (bus, ticket, budget, view, timing, entry fee, best time ...),
  writing those English words in Telugu script so the voice pronounces them naturally;
- talk TO the viewer ("friends", "mana", "chudandi"), react to what's on screen, add small opinions,
  a bit of humour, and practical tips (how to reach, rough budget, best time, what to skip);
- vary sentence length; short punchy lines; no lists, no emojis, no stage directions;
- NEVER use clichés like "hidden gem", "nestled", "tapestry", "paradise on earth", "a feast for the eyes",
  "embark on a journey", "in this video we will", "without further ado";
- never claim personal experiences that didn't happen (no "I ate here yesterday", "when I landed");
  present it as a guide: "ikkada vachhinappudu ...", "meeru vellinappudu ...";
- every fact (timings, fees, history) must be well known and stable; if unsure, leave it out;
- the narration is entirely your own words: never copy or translate anyone else's video.
search_terms and spot names are always in English, even when the narration is not."""

VIDEO_RULES = """You are given {n} source videos (Creative Commons, reuse allowed) about this destination.
Watch them and build the episode ONLY from places that are clearly shown in them:
- pick up to {k} places, in a sensible route order, and give each one exact shot timestamps;
- skip intros, outros, talking heads, title cards, subscribe screens, text overlays and logos;
- prefer wide, steady, beautiful shots; never use a shot that shows a different place;
- the narration for each place must match what those shots show."""


def _prompt(city, country, settings):
    lay = layout(settings)
    where = f"{city}, {country}" if country and country.lower() != city.lower() else city
    p = (f"Destination this week: {where}.\n"
         f"Narration language: {settings['language']}. The YouTube title and description should "
         f"also be in {settings['language']}, with the place names in English.\n"
         f"Host personality: {settings['host_style']}.\n\n"
         f"LONG VIDEO, about {lay['minutes']:.0f} minutes: pick the {lay['places']} best places, in a sensible "
         f"route order (mix famous sights with food streets, markets, viewpoints and day trips). "
         f"Each place's narration must be about {lay['place_words']} words (~{lay['place_secs']:.0f} seconds "
         f"spoken): what we're seeing, a little history or a story, how to reach, timings and rough budget, "
         f"food nearby, an honest opinion or tip. Keep it flowing and varied, never padded or repetitive. "
         f"Intro and outro: 15-25 words each.\n")
    if settings.get("shorts", {}).get("enabled"):
        p += (f"\nSHORT, {lay['short_secs']:.0f}-second vertical YouTube Short teasing the long video: "
              f"a hook, then {lay['short_places']} beats for the {lay['short_places']} most stunning places "
              f"(about {lay['beat_words']} words each, fast and exciting), then the outro.\n")
    return p


def _ask(contents, schema, system):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(timeout=600_000))  # ms; a long script can take minutes
    response = gemini_models.generate(
        client, MODEL, "text", contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,  # keeps long videos cheap
        ),
    )
    return response.parsed if response.parsed is not None else schema.model_validate_json(response.text)


def make_plan(city: str, country: str, settings: dict) -> VideoPlan:
    script = _ask(_prompt(city, country, settings), _Script, SYSTEM)
    return _fix_short(VideoPlan(city=city, country=country, **script.model_dump()))


def _fix_short(plan: VideoPlan) -> VideoPlan:
    """Drop Short beats that point at a place that doesn't exist, and repeats."""
    seen, beats = set(), []
    for b in plan.short_beats:
        if 1 <= b.spot <= len(plan.spots) and b.spot not in seen:
            seen.add(b.spot)
            beats.append(b)
    plan.short_beats = beats
    return plan


def make_plan_from_videos(city: str, country: str, sources: list[SourceVideo], settings: dict) -> VideoPlan:
    parts = []
    for i, s in enumerate(sources, 1):
        parts.append(types.Part(text=f"Source video {i}: \"{s.title}\" by {s.channel}"))
        parts.append(types.Part(file_data=types.FileData(file_uri=s.url, mime_type="video/*")))
    parts.append(types.Part(text=_prompt(city, country, settings)))
    system = SYSTEM + "\n\n" + VIDEO_RULES.format(n=len(sources), k=layout(settings)["places"])
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
    return _fix_short(plan)
