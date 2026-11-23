"""Uses Gemini to pick the places in a city and write the voiceover script."""
import os

from google import genai
from pydantic import BaseModel, Field


class Spot(BaseModel):
    name: str = Field(description="Place name in English, short enough for an on-screen title")
    search_terms: list[str] = Field(
        description="2-3 short English phrases for searching video footage of this exact place, "
                    "most specific first, e.g. ['Hawa Mahal Jaipur', 'Hawa Mahal facade']")
    narration: str = Field(description="Voiceover for this place, 35-55 words, in the requested language")


class VideoPlan(BaseModel):
    city: str
    country: str
    title: str = Field(description="Catchy YouTube title, under 70 characters")
    intro_narration: str = Field(description="Hook for the first ~8 seconds, 15-25 words")
    spots: list[Spot]
    outro_narration: str = Field(description="Sign-off asking viewers to like/subscribe, 15-25 words")
    description: str = Field(description="YouTube description, 2-3 short paragraphs")
    tags: list[str] = Field(description="10-15 YouTube tags")


SYSTEM = """You write voiceover scripts for a weekly travel video channel.
Each episode covers one destination and its must-see places. The narration is read
by a text-to-speech voice over stock and Creative Commons footage, so:
- write for the ear: short sentences, no lists, no emojis, no stage directions;
- talk about what the viewer is seeing at that place;
- every fact (timings, fees, history) must be well known and stable; if unsure, leave it out.
search_terms and spot names are always in English, even when the narration is not."""


def make_plan(city: str, country: str, settings: dict) -> VideoPlan:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = (
        f"Destination this week: {city}, {country}.\n"
        f"Pick the {settings['places_per_video']} best places to visit there, in a sensible route order.\n"
        f"Narration language: {settings['language']}.\n"
        f"Host personality: {settings['host_style']}."
    )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
        config={
            "system_instruction": SYSTEM,
            "response_mime_type": "application/json",
            "response_schema": VideoPlan,
        },
    )
    if response.parsed is not None:
        return response.parsed
    return VideoPlan.model_validate_json(response.text)
