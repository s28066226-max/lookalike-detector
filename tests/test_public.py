"""Tests for the public-website protections: no internal addresses, rate limits."""

import pytest
from fastapi.testclient import TestClient

from detector import server
from detector.analyze import Report
from detector.checks import page


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(page, "PUBLIC_MODE", True)
    monkeypatch.setattr(server, "PUBLIC_MODE", True)
    server._recent.clear()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost:8000/api/health",    # the app itself
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata service
    "http://[::1]/",
    "file:///etc/passwd",
])
def test_internal_addresses_blocked(public, url):
    assert not page.is_allowed_url(url)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/",
    "http://localhost:8001/sbi/",   # our demo pages, inside the server
    "data:image/png;base64,AAAA",
])
def test_public_and_demo_addresses_allowed(public, url):
    assert page.is_allowed_url(url)


def test_everything_allowed_on_a_laptop(monkeypatch):
    monkeypatch.setattr(page, "PUBLIC_MODE", False)
    assert page.is_allowed_url("http://192.168.1.1/")


def test_blocked_page_is_skipped_without_opening_browser(public, monkeypatch):
    def no_browser(url):
        raise AssertionError("browser must not open")
    monkeypatch.setattr(page, "fetch_page", no_browser)
    result = page.check_page("http://169.254.169.254/")
    assert result.screenshot is None
    assert "private or internal" in result.skipped[0]


def test_rate_limit(public, monkeypatch):
    fake = Report(url="u", host="example.com", registered_domain="example.com",
                  official_brand=None, target_brand=None)
    monkeypatch.setattr(server, "analyze", lambda url: fake)
    client = TestClient(server.app)
    codes = [client.post("/api/check", json={"url": "example.com"}).status_code for _ in range(7)]
    assert codes == [200] * 6 + [429]
