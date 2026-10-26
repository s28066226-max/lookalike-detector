"""Tests for the address check. No network: WHOIS is disabled or faked."""

from datetime import datetime, timedelta, timezone

import pytest

from detector.checks import domain
from detector.checks.domain import check_domain, skeleton
from detector.verdict import risk_score, verdict_for


def codes(url):
    return {s.code for s in check_domain(url, use_whois=False).signals}


def verdict(url):
    return verdict_for(risk_score(check_domain(url, use_whois=False).signals))


# --- Official domains -------------------------------------------------------

@pytest.mark.parametrize("url, brand", [
    ("https://www.onlinesbi.sbi", "sbi"),
    ("https://retail.onlinesbi.sbi/retail/login.htm", "sbi"),
    ("https://sbi.bank.in", "sbi"),
    ("sbi.co.in", "sbi"),                      # no scheme typed
    ("https://netbanking.hdfcbank.com", "hdfc"),
    ("https://www.icicibank.com", "icici"),
    ("https://www.irctc.co.in/nget/train-search", "irctc"),
    ("https://myaadhaar.uidai.gov.in", "uidai"),
    ("https://www.incometax.gov.in/iec/foportal/", "incometax"),
    ("https://unifiedportal-mem.epfindia.gov.in", "epfo"),
    ("HTTPS://WWW.AXISBANK.COM/", "axis"),     # case-insensitive
])
def test_official_domains_are_genuine(url, brand):
    result = check_domain(url, use_whois=False)
    assert result.official_brand == brand
    assert verdict_for(risk_score(result.signals)) == "genuine"


def test_official_domain_over_http_is_still_genuine():
    assert codes("http://www.sbi.co.in") == {"official_domain", "no_https"}
    assert verdict("http://www.sbi.co.in") == "genuine"


def test_bank_in_does_not_make_every_bank_in_site_official():
    result = check_domain("https://sbi-kyc.bank.in", use_whois=False)
    assert result.official_brand is None
    assert "brand_in_domain" in codes("https://sbi-kyc.bank.in")


# --- Look-alike domain names ------------------------------------------------

def test_homoglyph_l_for_i():
    result = check_domain("https://onlinesbl.com", use_whois=False)
    assert result.target_brand == "sbi"
    assert "homoglyph" in codes("https://onlinesbl.com")


def test_homoglyph_digit():
    assert "homoglyph" in codes("https://icic1bank.com")


def test_cyrillic_letter_unicode_form():
    url = "https://onlinеsbi.com"  # Cyrillic е
    assert {"idn_host", "homoglyph"} <= codes(url)
    assert verdict(url) == "likely_fake"


def test_cyrillic_letter_punycode_form():
    url = "https://xn--onlinsbi-g8g.com"  # same domain, written as punycode
    result = check_domain(url, use_whois=False)
    assert result.host == "onlinеsbi.com"
    assert {"idn_host", "homoglyph"} <= codes(url)


def test_typo_transposed_letters():
    result = check_domain("https://hdfcbnak.com", use_whois=False)
    assert result.target_brand == "hdfc"
    assert "typosquat" in codes("https://hdfcbnak.com")


def test_same_name_wrong_ending():
    assert "wrong_tld" in codes("https://hdfcbank.co")
    assert "wrong_tld" in codes("https://sbi.com")


def test_short_names_need_exact_skeleton_not_edit_distance():
    # "pnc" is one letter from "pnb" but is a different, real bank.
    assert codes("https://pnc.com") == set()


# --- Brand name on someone else's domain ------------------------------------

def test_brand_in_subdomain():
    url = "https://sbi.secure-login.xyz"
    assert {"brand_in_subdomain", "suspicious_tld"} <= codes(url)
    assert check_domain(url, use_whois=False).target_brand == "sbi"


def test_brand_in_domain_name():
    assert "brand_in_domain" in codes("https://hdfcbank-kyc-update.com")
    assert "brand_in_domain" in codes("https://update-aadhaar-online.in")


def test_brand_in_path():
    assert "brand_in_path" in codes("https://example.com/irctc/refund")


def test_short_keyword_does_not_match_inside_other_words():
    assert codes("https://lesbian.org") == set()


# --- Other address tricks ---------------------------------------------------

def test_ip_address_host():
    url = "http://192.168.1.5/sbi/login"
    assert {"ip_address", "no_https", "brand_in_path"} <= codes(url)
    assert verdict(url) == "likely_fake"


def test_at_sign_hides_real_host():
    result = check_domain("https://www.sbi.co.in@evil.com/login", use_whois=False)
    assert result.host == "evil.com"
    assert result.official_brand is None
    assert result.target_brand == "sbi"
    assert "userinfo_trick" in {s.code for s in result.signals}


def test_unrelated_site_has_no_signals():
    assert codes("https://google.com") == set()
    assert verdict("https://google.com") == "genuine"


def test_empty_url():
    assert "invalid_url" in codes("")


def test_skeleton():
    assert skeleton("onlinesbl") == skeleton("onlinesbi") == skeleton("onl1nesbi")
    assert skeleton("rnybank") == skeleton("mybank")


# --- Domain age (WHOIS faked) -----------------------------------------------

def fake_whois(monkeypatch, value):
    def fake(_domain):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(domain, "_lookup_creation_date", fake)


def test_new_domain(monkeypatch):
    fake_whois(monkeypatch, datetime.now(timezone.utc) - timedelta(days=5))
    result = check_domain("https://sbi-rewards.top")
    assert "new_domain" in {s.code for s in result.signals}


def test_old_domain(monkeypatch):
    fake_whois(monkeypatch, datetime(2001, 1, 1, tzinfo=timezone.utc))
    result = check_domain("https://example.com")
    assert [s.code for s in result.signals] == ["domain_age"]


def test_whois_failure_is_skipped_not_crash(monkeypatch):
    fake_whois(monkeypatch, ConnectionError("timeout"))
    result = check_domain("https://onlinesbl.com")
    assert "homoglyph" in {s.code for s in result.signals}
    assert any("WHOIS lookup failed" in s for s in result.skipped)


def test_whois_not_called_for_official_domains(monkeypatch):
    fake_whois(monkeypatch, AssertionError("should not be called"))
    result = check_domain("https://www.hdfcbank.com")
    assert result.skipped == []


@pytest.mark.parametrize("value, expected_year", [
    (datetime(2010, 5, 1), 2010),
    ([datetime(2012, 1, 1), datetime(2010, 1, 1)], 2010),  # earliest wins
    (None, None),
    ("2010-05-01", None),
    ([], None),
])
def test_pick_creation_date(value, expected_year):
    picked = domain._pick_creation_date(value)
    assert (picked.year if picked else None) == expected_year
