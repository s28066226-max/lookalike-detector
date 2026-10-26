"""Tests for the HTML part of the page check (no browser needed)."""

from pathlib import Path

from detector.checks.page import analyze_html

DEMO_SBI = (Path(__file__).resolve().parent.parent / "demo_sites" / "sbi" / "index.html").read_text()


def codes(html, url):
    return {s.code for s in analyze_html(html, url)}


def test_demo_sbi_page():
    assert codes(DEMO_SBI, "http://localhost:8001/sbi/") == {
        "asks_password", "asks_otp", "asks_card", "form_posts_elsewhere", "brand_in_page_text",
    }


def test_same_page_on_official_domain_has_no_brand_or_form_flags():
    html = DEMO_SBI.replace("https://sbi-kyc-verify.example/collect", "/login")
    found = codes(html, "https://retail.onlinesbi.sbi/retail/login.htm")
    assert "brand_in_page_text" not in found
    assert "form_posts_elsewhere" not in found


def test_form_posting_to_other_official_domain_of_same_brand_is_fine():
    html = '<form action="https://retail.onlinesbi.sbi/login"><input type="password"></form>'
    assert codes(html, "https://www.sbi.co.in/") == {"asks_password"}


def test_form_posting_to_relative_url_is_fine():
    html = '<form action="/submit"><input name="q"></form>'
    assert codes(html, "https://example.com/") == set()


def test_form_posting_to_email():
    html = '<form action="mailto:thief@example.com"><input name="x"></form>'
    assert "form_posts_email" in codes(html, "https://example.com/")


def test_form_posting_over_http_from_https_page():
    html = '<form action="http://example.com/login"><input type="password"></form>'
    assert "form_posts_http" in codes(html, "https://example.com/")


def test_identity_fields():
    html = '<form><label for="a">Aadhaar number</label><input id="a"><input placeholder="PAN"></form>'
    assert "asks_identity" in codes(html, "https://example.com/")


def test_hidden_fields_are_ignored():
    html = '<form><input type="hidden" name="otp_token"></form>'
    assert codes(html, "https://example.com/") == set()


def test_label_of_another_field_is_not_used():
    html = '<label for="o">OTP</label><input id="o"><input id="name" name="fullname">'
    assert codes(html, "https://example.com/") == {"asks_otp"}


def test_brand_name_needs_whole_word():
    html = "<title>SBIx Gaming Forum</title>"
    assert codes(html, "https://example.com/") == set()


def test_hindi_brand_name_in_heading():
    html = "<h1>भारतीय स्टेट बैंक</h1>"
    assert "brand_in_page_text" in codes(html, "https://example.com/")


def test_official_site_mentioning_another_brand_is_fine():
    # Real bank pages often mention Aadhaar, RBI etc. That is not impersonation.
    html = "<title>Bank of Baroda</title><h2>Link your Aadhaar</h2>"
    assert codes(html, "https://bankofbaroda.bank.in/") == set()
