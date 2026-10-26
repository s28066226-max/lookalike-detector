"""Tests for the visual brand check and the combined analysis (model and browser faked)."""

import pytest

from detector import analyze as analyze_module
from detector import llm
from detector.checks.domain import Signal
from detector.checks.page import PageResult
from detector.checks.vision import BrandResult, brand_signals, parse_brand_json


# --- Parsing the model's reply ----------------------------------------------

def test_parse_plain_json():
    r = parse_brand_json('{"brand": "sbi", "confidence": 0.9, "evidence": ["blue keyhole logo"]}')
    assert r == BrandResult("sbi", 0.9, ["blue keyhole logo"])


def test_parse_json_in_code_fence_with_chatter():
    text = 'Sure!\n```json\n{"brand": "HDFC", "confidence": "0.8", "evidence": []}\n```'
    r = parse_brand_json(text)
    assert r.brand == "hdfc" and r.confidence == 0.8


def test_parse_null_brand():
    assert parse_brand_json('{"brand": null, "confidence": 0.7}') == BrandResult(None, 0.0, [])


def test_parse_unknown_brand_becomes_none():
    assert parse_brand_json('{"brand": "paypal", "confidence": 0.95}').brand is None


def test_parse_clamps_confidence():
    assert parse_brand_json('{"brand": "sbi", "confidence": 7}').confidence == 1.0


def test_parse_no_json_raises():
    with pytest.raises(ValueError):
        parse_brand_json("I cannot see the image")


# --- Our rule on top of the model's answer ----------------------------------

def test_brand_on_wrong_domain_is_red_flag():
    [s] = brand_signals(BrandResult("sbi", 0.9, ["SBI logo"]), "localhost")
    assert s.code == "visual_brand_mismatch" and s.points > 0


def test_brand_on_official_domain_is_fine():
    [s] = brand_signals(BrandResult("sbi", 0.9), "retail.onlinesbi.sbi")
    assert s.code == "visual_brand_match" and s.points == 0


def test_low_confidence_does_not_count():
    [s] = brand_signals(BrandResult("sbi", 0.3), "example.com")
    assert s.points == 0


def test_no_brand_no_signal():
    assert brand_signals(BrandResult(None, 0.0), "example.com") == []


# --- Combined analysis ------------------------------------------------------

@pytest.fixture
def fake_page(monkeypatch):
    def fake_check_page(url):
        return PageResult(final_url=url, title="Fake", screenshot=b"png",
                          signals=[Signal("asks_password", "low", 10, "asks for password")])
    monkeypatch.setattr(analyze_module, "check_page", fake_check_page)


def test_analyze_combines_all_checks(monkeypatch, fake_page):
    monkeypatch.setattr(analyze_module, "describe_brand", lambda png: BrandResult("sbi", 0.9, ["logo"]))
    report = analyze_module.analyze("https://sbi.secure-login.xyz", use_whois=False)
    found = {s.code for s in report.signals}
    assert {"brand_in_subdomain", "suspicious_tld", "asks_password", "visual_brand_mismatch"} <= found
    assert report.verdict == "likely_fake"


def test_analyze_without_model_still_gives_verdict(monkeypatch, fake_page):
    def no_model(png):
        raise llm.ModelUnavailable("GEMINI_API_KEY is not set in .env")
    monkeypatch.setattr(analyze_module, "describe_brand", no_model)
    report = analyze_module.analyze("https://sbi.secure-login.xyz", use_whois=False)
    assert report.score == 65  # 40 brand_in_subdomain + 15 tld + 10 password
    assert report.verdict == "likely_fake"
    assert any("Visual brand check" in s for s in report.skipped)


def test_analyze_model_error_is_skipped(monkeypatch, fake_page):
    def broken(png):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(analyze_module, "describe_brand", broken)
    report = analyze_module.analyze("https://www.hdfcbank.com", use_whois=False)
    assert report.verdict == "genuine"
    assert any("model error" in s for s in report.skipped)


def test_analyze_follows_redirect_to_lookalike(monkeypatch):
    def redirected(url):
        return PageResult(final_url="https://hdfcbank-kyc.xyz/login",
                          signals=[Signal("redirected", "info", 0, "redirected")])
    monkeypatch.setattr(analyze_module, "check_page", redirected)
    report = analyze_module.analyze("https://bit.ly/abc", use_whois=False, use_vision=False)
    assert any(s.code == "brand_in_domain" and s.detail.startswith("After redirect") for s in report.signals)
    assert report.target_brand == "hdfc"


def test_analyze_skips_model_on_error_page(monkeypatch):
    def blocked(url):
        return PageResult(final_url=url, screenshot=b"png", http_status=403)

    def must_not_call(png):
        raise AssertionError("model should not see an error page")
    monkeypatch.setattr(analyze_module, "check_page", blocked)
    monkeypatch.setattr(analyze_module, "describe_brand", must_not_call)
    report = analyze_module.analyze("https://www.hdfcbank.com", use_whois=False)
    assert report.verdict == "genuine"
    assert any("HTTP 403" in s for s in report.skipped)
