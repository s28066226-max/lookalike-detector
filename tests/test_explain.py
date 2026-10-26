"""Tests for the plain-language explanation and the web API (model faked)."""

import pytest
from fastapi.testclient import TestClient

from detector import explain as explain_module
from detector import llm, server
from detector.analyze import Report
from detector.checks.domain import Signal
from detector.explain import explain, fallback_bullets, parse_bullets

FAKE_SIGNALS = [
    Signal("no_https", "medium", 15, "The connection is not encrypted"),
    Signal("redirected", "info", 0, "The address redirected"),
    Signal("form_posts_elsewhere", "high", 35, "The form sends what you type to a different site"),
]


def test_parse_bullets_with_fence():
    assert parse_bullets('```json\n["एक", "दो"]\n```') == ["एक", "दो"]


def test_parse_bullets_keeps_at_most_five():
    assert len(parse_bullets(str([f"b{i}" for i in range(8)]).replace("'", '"'))) == 5


def test_parse_bullets_rejects_empty():
    with pytest.raises(ValueError):
        parse_bullets("[]")


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
def test_fallback_has_every_language(lang):
    bullets = fallback_bullets(FAKE_SIGNALS, "likely_fake", "sbi", lang)
    # biggest signal first, info signals skipped, advice last
    assert len(bullets) == 3
    assert bullets[0] == explain_module._fallback_text()["signals"]["form_posts_elsewhere"][lang]
    assert bullets[-1] == explain_module._fallback_text()["advice"]["likely_fake"][lang]


def test_fallback_uses_hindi_brand_name():
    signals = [Signal("visual_brand_mismatch", "high", 45, "looks like SBI")]
    assert "भारतीय स्टेट बैंक" in fallback_bullets(signals, "likely_fake", "sbi", "hi")[0]


def test_every_scoring_signal_has_fallback_text():
    texts = explain_module._fallback_text()
    scoring_codes = [
        "official_domain", "invalid_url", "userinfo_trick", "ip_address", "no_https", "idn_host",
        "homoglyph", "typosquat", "wrong_tld", "brand_in_domain", "brand_in_subdomain", "brand_in_path",
        "suspicious_tld", "new_domain", "young_domain", "bad_certificate", "asks_password", "asks_otp",
        "asks_card", "asks_identity", "form_posts_elsewhere", "form_posts_email", "form_posts_http",
        "brand_in_page_text", "visual_brand_mismatch",
    ]
    for code in scoring_codes:
        assert set(texts["signals"][code]) == {"en", "hi", "mr"}, code


def test_explain_uses_model_reply(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: '["Fake site.", "Close it."]')
    assert explain(FAKE_SIGNALS, "likely_fake", "x.com", "sbi", "en") == (["Fake site.", "Close it."], "model")


def test_explain_prompt_contains_only_scoring_facts(monkeypatch):
    seen = {}

    def fake(prompt, **kw):
        seen["prompt"] = prompt
        return '["ok"]'
    monkeypatch.setattr(llm, "generate", fake)
    explain(FAKE_SIGNALS, "likely_fake", "x.com", "sbi", "hi")
    assert "different site" in seen["prompt"] and "Hindi" in seen["prompt"]
    assert "redirected" not in seen["prompt"]


def test_explain_falls_back_without_model(monkeypatch):
    def no_model(prompt, **kw):
        raise llm.ModelUnavailable("no key")
    monkeypatch.setattr(llm, "generate", no_model)
    bullets, source = explain(FAKE_SIGNALS, "likely_fake", "x.com", "sbi", "mr")
    assert source == "fallback" and bullets


# --- Web API ----------------------------------------------------------------

client = TestClient(server.app)


def test_index_page_served():
    res = client.get("/")
    assert res.status_code == 200 and "Look-Alike" in res.text


def test_check_endpoint(monkeypatch):
    fake = Report(url="https://sbi.x.xyz", host="sbi.x.xyz", registered_domain="x.xyz",
                  official_brand=None, target_brand="sbi", signals=FAKE_SIGNALS,
                  score=50, verdict="suspicious", screenshot=b"\x89PNG")
    monkeypatch.setattr(server, "analyze", lambda url: fake)
    data = client.post("/api/check", json={"url": "https://sbi.x.xyz"}).json()
    assert data["verdict"] == "suspicious"
    assert data["screenshot"].startswith("data:image/png;base64,")
    assert data["brand_names"]["hi"] == "भारतीय स्टेट बैंक"
    assert set(data["quick_explanation"]) == {"en", "hi", "mr"}


def test_explain_endpoint(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: '["एक", "दो", "तीन"]')
    body = {"host": "x.com", "verdict": "likely_fake", "brand": "sbi", "lang": "hi",
            "signals": [{"code": "no_https", "severity": "medium", "points": 15, "detail": "no https"}]}
    data = client.post("/api/explain", json=body).json()
    assert data == {"bullets": ["एक", "दो", "तीन"], "source": "model", "model": llm.model_name(), "lang": "hi"}
