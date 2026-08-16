"""Stage 4: LLM adjudication of the mapping residue.

The model never generates a LOINC code. It is handed a numbered list that
stage 2 built deterministically from the LOINC table and asked to return one
index, or NONE. Post-validation rejects anything that is not an index into
that list, so a hallucinated code cannot reach the database -- the failure mode
degrades from "invented a code" to "picked the wrong option", which the
confidence floor and the review queue already handle.

The only untrusted text that reaches the model is a <=60 character test name
lifted from a PDF. It carries no instructions worth following and the output
grammar is a single integer, so prompt injection has essentially no surface.

De-identification: the prompt contains the test name, the unit, and the
specimen. Never the patient, the date, or the *value*.
"""

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger("labledger.llm")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You are matching a laboratory test name to a LOINC code.

Observed test name: {name}
Unit as printed:    {unit}
Specimen:           {specimen}

Candidates:
{candidates}

Reply with ONLY the number of the best match, or NONE if no candidate is correct.
Do not explain. Do not output a LOINC code."""

_INDEX = re.compile(r"-?\d+")


class LLMUnavailableError(Exception):
    """The adjudicator cannot be reached.

    No key, quota exhausted, or the API is down. Callers fall back to the
    review queue rather than retrying into oblivion or blocking an upload.
    """


async def adjudicate(
    name: str,
    unit: str | None,
    specimen: str | None,
    candidates: list,           # list[Candidate] from mapping.py
    model: str | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 - passed to httpx, not an asyncio timeout
) -> tuple[str | None, str]:
    """-> (chosen loinc_code or None, model_used)."""
    if not settings.gemini_api_key:
        raise LLMUnavailableError("GEMINI_API_KEY not set")
    if not candidates:
        return None, model or settings.gemini_model

    model = model or settings.gemini_model
    listing = "\n".join(f"{i}. {c.display}" for i, c in enumerate(candidates))
    prompt = PROMPT.format(
        name=name[:60], unit=unit or "not printed",
        specimen=specimen or "not stated", candidates=listing,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                ENDPOINT.format(model=model),
                params={"key": settings.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    # temperature 0: the same row must resolve the same way twice.
                    "generationConfig": {"temperature": 0, "maxOutputTokens": 2000},
                },
            )
    except (TimeoutError, httpx.HTTPError) as exc:
        raise LLMUnavailableError(f"{type(exc).__name__}") from exc

    if r.status_code == 429:
        raise LLMUnavailableError("quota exceeded")
    if r.status_code != 200:
        raise LLMUnavailableError(f"HTTP {r.status_code}")

    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        return None, model

    if text.upper().startswith("NONE"):
        return None, model

    m = _INDEX.search(text)
    if not m:
        return None, model
    idx = int(m.group())
    # The whole safety property: an index outside the offered list is discarded.
    if not (0 <= idx < len(candidates)):
        logger.warning("llm returned out-of-range index %d for %d candidates",
                       idx, len(candidates))
        return None, model
    return candidates[idx].loinc_code, model
