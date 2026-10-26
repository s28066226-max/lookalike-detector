"""Page check: open the URL in a headless browser, take a screenshot and look
at the rendered HTML (no AI).

fetch_page() talks to the network; analyze_html() is pure and easy to test.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from detector.checks.domain import (
    Signal, brand_name, find_official_brand, get_registered_domain, load_brands, to_ascii,
)

OTP_RE = re.compile(r"\botp\b|one.?time|\bm?pin\b|verification.?code", re.I)
CARD_RE = re.compile(r"card.?(no|num)|\bcvv\b|\bcvc\b|expiry|exp.?date|debit|credit|atm", re.I)
IDENTITY_RE = re.compile(r"aadhaa?r|\bpan\b|pan.?(no|num|card)|date.?of.?birth|\bdob\b", re.I)


@dataclass
class PageResult:
    final_url: str = ""
    title: str = ""
    http_status: Optional[int] = None   # 403, 404... means we got an error page, not the site
    screenshot: Optional[bytes] = None  # PNG of the visible part of the page
    signals: List[Signal] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def _host(url: str) -> str:
    return to_ascii((urlsplit(url).hostname or "").rstrip("."))


def _site(url: str) -> str:
    """Registered domain of a URL, or the bare host for IPs/localhost."""
    host = _host(url)
    return get_registered_domain(host) or host


def _field_text(tag) -> str:
    """Everything that describes an input field, joined for regex matching."""
    attrs = ["name", "id", "placeholder", "autocomplete", "aria-label", "title"]
    text = " ".join(str(tag.get(a, "")) for a in attrs)
    # The field's own label: <label for="id"> or a <label> wrapped around it
    label = tag.find_parent("label")
    if not label and tag.get("id"):
        label = tag.find_previous("label", attrs={"for": tag["id"]})
    if label:
        text += " " + label.get_text(" ", strip=True)
    return text


def _brand_in_page(soup) -> Optional[str]:
    """Brand whose name appears in the title, headings, logo alt text or site name."""
    parts = [soup.title.get_text(" ") if soup.title else ""]
    parts += [h.get_text(" ") for h in soup.find_all(["h1", "h2", "h3"])]
    parts += [img.get("alt", "") for img in soup.find_all("img")]
    parts += [m.get("content", "") for m in soup.find_all("meta", property="og:site_name")]
    text = " ".join(parts)
    for brand_id, brand in load_brands().items():
        for name in brand["display_names"]:
            # Whole-word match for Latin names ("SBI" but not "SBIx"); plain
            # substring for Hindi, where \b doesn't work well.
            pattern = rf"\b{re.escape(name)}\b" if name.isascii() else re.escape(name)
            if re.search(pattern, text, re.I):
                return brand_id
    return None


def analyze_html(html: str, page_url: str) -> List[Signal]:
    soup = BeautifulSoup(html, "html.parser")
    signals = []
    page_host = _host(page_url)
    page_site = _site(page_url)
    official = find_official_brand(page_host)

    inputs = [i for i in soup.find_all(["input", "select", "textarea"])
              if str(i.get("type", "")).lower() not in ("hidden", "submit", "button")]
    texts = [_field_text(i) for i in inputs]

    if any(str(i.get("type", "")).lower() == "password" for i in inputs):
        signals.append(Signal("asks_password", "low", 10, "The page asks for a password"))
    if any(OTP_RE.search(t) for t in texts):
        signals.append(Signal("asks_otp", "low", 10, "The page asks for an OTP or PIN"))
    if any(CARD_RE.search(t) for t in texts):
        signals.append(Signal("asks_card", "medium", 15, "The page asks for card details (number, CVV or expiry)"))
    if any(IDENTITY_RE.search(t) for t in texts):
        signals.append(Signal("asks_identity", "low", 10, "The page asks for Aadhaar, PAN or date of birth"))

    # Where does the typed data go? A form that sends it to another site is
    # the classic phishing pattern. Sending to the brand's other official
    # domain (sbi.co.in -> onlinesbi.sbi) is fine.
    for form in soup.find_all("form"):
        action = (form.get("action") or "").strip()
        if not action or action.startswith(("#", "javascript:")):
            continue
        if action.lower().startswith("mailto:"):
            signals.append(Signal("form_posts_email", "high", 35,
                "The form sends what you type to an e-mail address"))
            break
        target = urljoin(page_url, action)
        target_site = _site(target)
        if target_site and target_site != page_site and not find_official_brand(_host(target)):
            signals.append(Signal("form_posts_elsewhere", "high", 35,
                f"The form sends what you type to a different site ({target_site})"))
            break
        if target.startswith("http://") and page_url.startswith("https://"):
            signals.append(Signal("form_posts_http", "medium", 15,
                "The form sends what you type without encryption"))
            break

    # Text-only brand check. Also a fallback when the vision model is off.
    page_brand = _brand_in_page(soup)
    if page_brand and page_brand != official:
        signals.append(Signal("brand_in_page_text", "medium", 20,
            f"The page calls itself {brand_name(page_brand)}, but {page_site} is not its official site"))

    return signals


def fetch_page(url: str, timeout_ms: int = 15000):
    """Open the URL in headless Chromium. Returns (final_url, html, png, cert_ok, status)."""
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            cert_ok = True
            for ignore_https_errors in (False, True):
                context = browser.new_context(viewport={"width": 1280, "height": 800},
                                              ignore_https_errors=ignore_https_errors)
                page = context.new_page()
                try:
                    response = page.goto(url, wait_until="load", timeout=timeout_ms)
                    break
                except PlaywrightError as e:
                    # Bad certificate: note it, then retry so we can still look at the page.
                    if "ERR_CERT" in str(e) and not ignore_https_errors:
                        cert_ok = False
                        context.close()
                        continue
                    raise
            page.wait_for_timeout(800)  # let late JavaScript finish drawing
            status = response.status if response else None
            return page.url, page.content(), page.screenshot(type="png"), cert_ok, status
        finally:
            browser.close()


def check_page(url: str) -> PageResult:
    result = PageResult()
    if "://" not in url:
        url = "https://" + url
    if urlsplit(url).scheme not in ("http", "https"):
        result.skipped.append("Page check (only http/https addresses can be opened)")
        return result

    try:
        final_url, html, png, cert_ok, status = fetch_page(url)
    except Exception as e:  # site down, timeout, browser missing...
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        result.skipped.append(f"Page check (could not open the page: {msg[:120]})")
        return result

    result.final_url = final_url
    result.screenshot = png
    result.http_status = status
    if status and status >= 400:
        # Many real banks block headless browsers (HDFC answers 403), so this
        # is not a red flag by itself. We just can't judge the real page.
        result.signals.append(Signal("http_error", "info", 0,
            f"The site answered with an error page (HTTP {status}); it may block automated checks"))
    soup_title = BeautifulSoup(html, "html.parser").title
    result.title = soup_title.get_text(" ", strip=True) if soup_title else ""

    if not cert_ok:
        result.signals.append(Signal("bad_certificate", "high", 35,
            "The site's security certificate is invalid"))
    if _site(final_url) != _site(url):
        result.signals.append(Signal("redirected", "info", 0,
            f"The address redirected to {_site(final_url)}"))
    result.signals.extend(analyze_html(html, final_url))
    return result
