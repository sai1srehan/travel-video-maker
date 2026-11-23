"""Picks a Gemini model that your API key can actually use.

Google retires model names over time (e.g. gemini-2.5-flash is closed to new users), so instead of
hard-coding one name we ask the API which models exist and take the newest suitable "flash" model.
A name set in GEMINI_MODEL / settings.json is always tried first.
"""
import re
import time

_cache: dict[str, str] = {}


def _version(name: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def _available(client) -> list[tuple[str, list[str]]]:
    out = []
    for m in client.models.list():
        name = (m.name or "").removeprefix("models/")
        actions = list(getattr(m, "supported_actions", None) or [])
        out.append((name, actions))
    return out


def candidates(client, preferred: str | None, kind: str = "text") -> list[str]:
    """All usable models in order of preference: `preferred` (if available), then the best others."""
    first = resolve(client, preferred, kind)
    return [first] + [m for m in _ranked(_available_cached(client), kind) if m != first]


_models_cache: list | None = None


def _available_cached(client):
    global _models_cache
    if _models_cache is None:
        _models_cache = _available(client)
    return _models_cache


def _ranked(models, kind):
    def ok(name, actions):
        if not name.startswith("gemini-") or "flash" not in name:
            return False
        if actions and "generateContent" not in actions:
            return False
        is_tts = "tts" in name
        if kind == "tts":
            return is_tts
        return not is_tts and not any(x in name for x in ("image", "live", "audio", "embedding", "thinking"))

    found = [n for n, a in models if ok(n, a)]
    # stable before preview/exp (previews get retired fast), then newest version, full flash before lite
    found.sort(key=lambda n: ("preview" not in n and "exp" not in n, _version(n), "lite" not in n), reverse=True)
    return found


def resolve(client, preferred: str | None, kind: str = "text") -> str:
    """kind: 'text' (scripts) or 'tts' (speech)."""
    key = f"{kind}:{preferred}"
    if key in _cache:
        return _cache[key]
    models = _available_cached(client)
    names = {n for n, _ in models}
    if preferred and preferred in names:
        _cache[key] = preferred
        return preferred

    ranked = _ranked(models, kind)
    if not ranked:
        raise RuntimeError(f"No Gemini {kind} model available for this API key. Models seen: {sorted(names)[:30]}")
    chosen = ranked[0]
    print(f"  (using Gemini model {chosen}" + (f" - '{preferred}' is not available)" if preferred else ")"), flush=True)
    _cache[key] = chosen
    return chosen


def _retryable(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return code in (429, 500, 502, 503, 504) or any(
        s in str(e) for s in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "high demand", "DEADLINE"))


def generate(client, preferred, kind, waits=(30, 60, 120, 240), log=print, **kwargs):
    """client.models.generate_content with retries on overload/rate limits, then other models."""
    last = None
    models = candidates(client, preferred, kind)
    for i, model in enumerate(models[:3]):  # the preferred model, then up to 2 fallbacks
        if i:
            log(f"  trying another Gemini model: {model}")
        for attempt, wait in enumerate((0,) + tuple(waits if i == 0 else waits[:2])):
            if wait:
                log(f"  Gemini busy ({str(last)[:80]}) - retrying in {wait}s")
                time.sleep(wait)
            try:
                return client.models.generate_content(model=model, **kwargs)
            except Exception as e:
                last = e
                if not _retryable(e):
                    break  # e.g. model not found / bad request: go to the next model
    raise last
