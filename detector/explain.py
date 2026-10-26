"""Plain-language explanation: turn triggered signals into 3-5 short bullets
in English, Hindi or Marathi.

Gemma 4 writes the bullets, using only the signals we pass in. If the model is
unavailable or replies badly, we fall back to fixed translated sentences, so
the user always gets an explanation.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from detector import llm
from detector.checks.domain import Signal, load_brands

LANGUAGES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "mr": "Marathi (Devanagari script)",
    "bn": "Bengali (Bengali script)",
    "gu": "Gujarati (Gujarati script)",
    "ta": "Tamil (Tamil script)",
    "te": "Telugu (Telugu script)",
}
VERDICT_WORDS = {"genuine": "genuine (no warning signs)", "suspicious": "suspicious",
                 "likely_fake": "likely fake (a look-alike website)"}
FALLBACK_FILE = Path(__file__).resolve().parent / "data" / "fallback_text.json"
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _fallback_text() -> dict:
    with open(FALLBACK_FILE, encoding="utf-8") as f:
        return json.load(f)


def brand_name_in(brand_id: Optional[str], lang: str) -> str:
    """Brand name in the user's language: the Devanagari name for hi/mr if we have one.
    Other languages use the English name, which people widely recognise (SBI, HDFC Bank)."""
    if not brand_id or brand_id not in load_brands():
        return ""
    names = load_brands()[brand_id]["display_names"]
    if lang in ("hi", "mr"):
        for name in names:
            if not name.isascii():
                return name
    return names[0]


def _relevant(signals: List[Signal]) -> List[Signal]:
    """Signals worth explaining: ones that changed the score, biggest first."""
    return sorted((s for s in signals if s.points != 0), key=lambda s: -abs(s.points))


def _prompt(signals: List[Signal], verdict: str, host: str, brand: str, lang: str) -> str:
    # Keep this prompt short. Gemma 4 thinks before answering and the API
    # can't turn that off; a long list of rules made it think for 2+ minutes
    # instead of ~20 seconds.
    facts = "\n".join(f"- {s.detail}" for s in _relevant(signals)) or "- No warning signs were found."
    brand_line = f"Brand: {brand}\n" if brand else ""
    return f"""Website: {host}
{brand_line}Verdict: {VERDICT_WORDS.get(verdict, verdict)}
Facts:
{facts}

In simple {LANGUAGES[lang]}, write 3-5 short bullets for a non-technical person explaining only these facts, last bullet = what to do. Never promise a site is completely safe. Output only a JSON array of strings."""


def parse_bullets(text: str) -> List[str]:
    """Pull the JSON array out of the model's reply (it may add ```fences```)."""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError("model reply contained no JSON array")
    items = json.loads(match.group(0))
    bullets = [str(b).strip() for b in items if str(b).strip()]
    if not bullets:
        raise ValueError("model reply had no bullets")
    return bullets[:5]


def signal_text(signal: Signal, brand_id: Optional[str], lang: str) -> str:
    """One finding as a plain sentence in the user's language (fixed text, no model)."""
    entry = _fallback_text()["signals"].get(signal.code)
    if not entry:
        return signal.detail
    return entry[lang].replace("{brand}", brand_name_in(brand_id, lang) or "the bank")


def fallback_bullets(signals: List[Signal], verdict: str, brand_id: Optional[str], lang: str) -> List[str]:
    bullets = []
    for s in _relevant(signals):
        sentence = signal_text(s, brand_id, lang)
        if sentence not in bullets:
            bullets.append(sentence)
    advice = _fallback_text()["advice"].get(verdict)
    return bullets[:4] + ([advice[lang]] if advice else [])


def explain(signals: List[Signal], verdict: str, host: str,
            brand_id: Optional[str], lang: str = "en") -> Tuple[List[str], str]:
    """Returns (bullets, source) where source is "model" or "fallback"."""
    if lang not in LANGUAGES:
        lang = "en"
    try:
        prompt = _prompt(signals, verdict, host, brand_name_in(brand_id, "en"), lang)
        # Long timeout is fine: the UI already shows the fixed-text bullets while waiting.
        return parse_bullets(llm.generate(prompt, timeout_s=120)), "model"
    except Exception as e:  # no key, network error, bad reply: use the fixed text
        log.warning("Explanation fell back to fixed text: %s: %s", type(e).__name__, e)
        return fallback_bullets(signals, verdict, brand_id, lang), "fallback"
