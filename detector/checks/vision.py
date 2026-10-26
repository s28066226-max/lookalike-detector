"""Visual brand check: ask Gemma 4 which brand the screenshot looks like.

The model only answers "which brand does this look like". Whether that is a
problem (brand seen on a domain it doesn't own) is decided by our own rules.
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from detector import llm
from detector.checks.domain import Signal, brand_name, find_official_brand, load_brands

MIN_CONFIDENCE = 0.6  # below this a brand guess is reported but not counted


@dataclass
class BrandResult:
    brand: Optional[str]  # brand id from brands.json, or None
    confidence: float
    evidence: List[str] = field(default_factory=list)


def _prompt() -> str:
    brand_lines = "\n".join(
        f'- "{bid}": {", ".join(b["display_names"])} (logo: {", ".join(b["logo_keywords"])})'
        for bid, b in load_brands().items()
    )
    return f"""You are checking a screenshot of a web page for brand impersonation.
Which of these Indian banks or government services does the page look like it belongs to?

{brand_lines}

Look at logos, names, colours and layout. If it does not clearly look like one of them, use null.
Reply with ONLY this JSON, nothing else:
{{"brand": "<one id from the list above, or null>", "confidence": <number 0 to 1>, "evidence": ["<short visible clue>", "..."]}}"""


def parse_brand_json(text: str) -> BrandResult:
    """Pull the JSON object out of the model's reply and validate it.

    Models sometimes wrap JSON in ```fences``` or add a sentence around it,
    so we take the first {...} block. Anything invalid becomes "no brand".
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model reply contained no JSON")
    data = json.loads(match.group(0))

    brand = data.get("brand")
    if isinstance(brand, str):
        brand = brand.strip().lower()
    if brand not in load_brands():
        brand = None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = [str(e) for e in data.get("evidence", []) if e][:5]
    return BrandResult(brand, confidence if brand else 0.0, evidence)


def describe_brand(image_png: bytes) -> BrandResult:
    return parse_brand_json(llm.generate(_prompt(), image_png))


def brand_signals(result: BrandResult, ascii_host: str) -> List[Signal]:
    """Our rule: a recognised brand on a domain that brand doesn't own is a red flag."""
    if not result.brand:
        return []
    name = brand_name(result.brand)
    clues = f" ({'; '.join(result.evidence[:3])})" if result.evidence else ""
    if find_official_brand(ascii_host) == result.brand:
        return [Signal("visual_brand_match", "info", 0,
                       f"The page looks like {name}, and this is its official site")]
    if result.confidence < MIN_CONFIDENCE:
        return [Signal("visual_brand_weak", "info", 0,
                       f"The page might resemble {name}, but the model is unsure{clues}")]
    return [Signal("visual_brand_mismatch", "high", 45,
                   f"The page looks like {name}{clues}, but this is not an official {name} site")]
