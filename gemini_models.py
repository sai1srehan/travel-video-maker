"""Picks a Gemini model that your API key can actually use.

Google retires model names over time (e.g. gemini-2.5-flash is closed to new users), so instead of
hard-coding one name we ask the API which models exist and take the newest suitable "flash" model.
A name set in GEMINI_MODEL / settings.json is always tried first.
"""
import re

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


def resolve(client, preferred: str | None, kind: str = "text") -> str:
    """kind: 'text' (scripts) or 'tts' (speech)."""
    key = f"{kind}:{preferred}"
    if key in _cache:
        return _cache[key]
    models = _available(client)
    names = {n for n, _ in models}
    if preferred and preferred in names:
        _cache[key] = preferred
        return preferred

    def ok(name, actions):
        if not name.startswith("gemini-") or "flash" not in name:
            return False
        if actions and "generateContent" not in actions:
            return False
        is_tts = "tts" in name
        if kind == "tts":
            return is_tts
        return not is_tts and not any(x in name for x in ("image", "live", "audio", "embedding", "thinking"))

    candidates = [n for n, a in models if ok(n, a)]
    if not candidates:
        raise RuntimeError(f"No Gemini {kind} model available for this API key. Models seen: {sorted(names)[:30]}")
    # stable before preview/exp (previews get retired fast), then newest version, full flash before lite
    candidates.sort(key=lambda n: ("preview" not in n and "exp" not in n, _version(n), "lite" not in n),
                    reverse=True)
    chosen = candidates[0]
    print(f"  (using Gemini model {chosen}" + (f" - '{preferred}' is not available)" if preferred else ")"), flush=True)
    _cache[key] = chosen
    return chosen
