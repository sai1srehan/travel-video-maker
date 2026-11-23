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
    text = f"{type(e).__name__} {e}"
    return code in (429, 500, 502, 503, 504) or any(
        s in text for s in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "high demand", "DEADLINE",
                            "Timeout", "timed out", "ConnectError", "RemoteProtocolError"))


def _out_of_quota(e: Exception) -> bool:
    """429 because a daily/free-tier quota is used up: waiting minutes won't help, use another model."""
    t = str(e)
    return "RESOURCE_EXHAUSTED" in t and any(s in t for s in ("PerDay", "per day", "free_tier", "FreeTier"))


def _short(e: Exception) -> str:
    t = str(e)
    m = re.search(r"'message': '([^']*)", t)
    return (m.group(1) if m else t)[:160]


def generate(client, preferred, kind, waits=(20, 45), log=print, budget_minutes=40, max_models=8, **kwargs):
    """client.models.generate_content that survives Gemini being overloaded.

    Goes through every usable model (preferred first, Lite and older versions after), a couple of
    quick retries each; if they're all busy, waits a few minutes and goes round again, until
    budget_minutes is used up. Models whose daily quota is spent are dropped for the rest of the run.
    """
    deadline = time.monotonic() + budget_minutes * 60
    models = candidates(client, preferred, kind)[:max_models]
    log(f"  Gemini models to try: {', '.join(models)}")
    dead: set[str] = set()
    last, rnd = None, 0
    while True:
        for model in models:
            if model in dead:
                continue
            for wait in (0,) + tuple(waits):
                if wait:
                    if time.monotonic() + wait > deadline:
                        break
                    log(f"    {model} busy - retrying in {wait}s")
                    time.sleep(wait)
                try:
                    out = client.models.generate_content(model=model, **kwargs)
                    if rnd or model != models[0]:
                        log(f"  OK with {model}")
                    return out
                except Exception as e:
                    last = e
                    log(f"    {model}: {type(e).__name__}: {_short(e)}")
                    if _out_of_quota(e) or not _retryable(e):
                        dead.add(model)  # quota gone / model not found / bad request
                        break
        rnd += 1
        cool = min(300, 60 * 2 ** rnd)  # 2, 4, 5, 5 ... minutes between rounds
        if len(dead) == len(models) or time.monotonic() + cool > deadline:
            raise last
        log(f"  all Gemini models busy - waiting {cool // 60} min, then trying again (round {rnd + 1})")
        time.sleep(cool)
