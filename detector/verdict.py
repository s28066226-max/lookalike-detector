"""Turn signals into a risk score and a verdict. Rule-based only: the AI model
never decides the verdict, it only supplies signals and explanation text."""

from typing import List

from detector.checks.domain import Signal

SUSPICIOUS_AT = 25   # score >= this -> "suspicious"
LIKELY_FAKE_AT = 60  # score >= this -> "likely_fake"


def risk_score(signals: List[Signal]) -> int:
    """Sum of signal points, clamped to 0..100."""
    return max(0, min(100, sum(s.points for s in signals)))


def verdict_for(score: int) -> str:
    if score >= LIKELY_FAKE_AT:
        return "likely_fake"
    if score >= SUSPICIOUS_AT:
        return "suspicious"
    return "genuine"
