"""Address check: looks only at the URL itself (no page fetch, no AI).

Produces a list of Signals. Each signal has a risk score contribution
(`points`); verdict.py adds them up. A WHOIS failure never crashes the
check: it is recorded in `skipped` and the other signals still count.
"""

import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import tldextract
from rapidfuzz.distance import OSA

BRANDS_FILE = Path(__file__).resolve().parent.parent / "data" / "brands.json"

# Use tldextract's bundled suffix list (no network call). "bank.in" is RBI's
# bank-only domain (sbi.bank.in, hdfc.bank.in), not yet in that list.
_extract = tldextract.TLDExtract(suffix_list_urls=(), extra_suffixes=["bank.in"])

# Cheap TLDs that show up a lot in phishing. Not proof, just a mild warning.
SUSPICIOUS_TLDS = {
    "xyz", "top", "club", "online", "site", "live", "icu", "buzz", "tk", "ml",
    "ga", "cf", "gq", "work", "click", "link", "info", "shop", "rest", "cyou",
    "sbs", "cfd", "monster", "support", "help", "vip", "win", "loan",
}

# Characters that look like other characters. Mapping both sides through this
# table gives a "skeleton": onlinesbl, onl1nesbi and onlinеsbi (Cyrillic е)
# all have the same skeleton as onlinesbi.
CONFUSABLES = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ԁ": "d", "ѕ": "s", "һ": "h", "к": "k", "м": "m",
    "т": "t", "в": "b", "н": "h", "ӏ": "l",
    # Greek
    "α": "a", "ο": "o", "ι": "i", "ν": "v", "ρ": "p", "κ": "k", "τ": "t", "υ": "u",
    # Digits and Latin letters that are easy to mix up
    "0": "o", "1": "l", "3": "e", "5": "s", "i": "l",
}
CONFUSABLE_PAIRS = [("rn", "m"), ("vv", "w")]


@dataclass
class Signal:
    code: str      # stable id, e.g. "homoglyph"
    severity: str  # "high" | "medium" | "low" | "info" | "good"
    points: int    # added to the risk score (negative = reassuring)
    detail: str    # short factual description, passed to the explanation step


@dataclass
class DomainResult:
    url: str
    host: str                     # readable form (Unicode for IDN hosts)
    ascii_host: str               # punycode form (xn--...)
    registered_domain: str        # e.g. "hdfcbank.com"; empty for IPs/localhost
    official_brand: Optional[str] = None  # brand id if this IS an official domain
    target_brand: Optional[str] = None    # brand id the URL seems to imitate
    signals: List[Signal] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def load_brands() -> Dict[str, dict]:
    with open(BRANDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def brand_name(brand_id: str) -> str:
    return load_brands()[brand_id]["display_names"][0]


def skeleton(text: str) -> str:
    text = text.lower()
    for a, b in CONFUSABLE_PAIRS:
        text = text.replace(a, b)
    return "".join(CONFUSABLES.get(ch, ch) for ch in text)


def _label_to_ascii(label: str) -> str:
    if label.isascii():
        return label
    try:
        return label.encode("idna").decode("ascii")
    except UnicodeError:
        return "xn--" + label.encode("punycode").decode("ascii")


def _label_to_unicode(label: str) -> str:
    if not label.startswith("xn--"):
        return label
    try:
        return label.encode("ascii").decode("idna")
    except UnicodeError:
        try:
            return label[4:].encode("ascii").decode("punycode")
        except UnicodeError:
            return label


def to_ascii(host: str) -> str:
    return ".".join(_label_to_ascii(p) for p in host.split("."))


def to_unicode(host: str) -> str:
    return ".".join(_label_to_unicode(p) for p in host.split("."))


def get_registered_domain(ascii_host: str) -> str:
    ext = _extract(ascii_host)
    return f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else ""


def find_official_brand(ascii_host: str) -> Optional[str]:
    """Brand id if the host is an official domain or a subdomain of one."""
    for brand_id, brand in load_brands().items():
        for domain in brand["official_domains"]:
            if ascii_host == domain or ascii_host.endswith("." + domain):
                return brand_id
    return None


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _keyword_in_token(keyword: str, token: str) -> bool:
    # Long keywords ("hdfcbank") may appear anywhere and are compared by
    # skeleton. Short ones ("sbi") must start or end a word, raw, so that
    # unrelated words like "lesbian" don't match.
    if len(keyword) >= 5:
        return skeleton(keyword) in skeleton(token)
    return token.startswith(keyword) or token.endswith(keyword)


def _brand_in_text(text: str) -> Optional[str]:
    """First brand whose keyword appears in `text`, or None."""
    tokens = _tokens(text)
    for brand_id, brand in load_brands().items():
        for kw in brand["keywords"]:
            if any(_keyword_in_token(kw, t) for t in tokens):
                return brand_id
    return None


def _find_lookalike(label: str, suffix: str):
    """Compare the domain name (without its ending) to every official domain.

    Returns (brand_id, official_domain, kind) or None. kind is one of:
      "wrong_tld"  same name, different ending  (hdfcbank.co vs hdfcbank.com)
      "homoglyph"  same after swapping look-alike chars (onlinesbl vs onlinesbi)
      "typo"       one or two letters off (hdfcbnak vs hdfcbank)
    """
    for brand_id, brand in load_brands().items():
        for official in brand["official_domains"]:
            if "." not in official:  # brand TLD like "sbi", nothing to compare
                continue
            off = _extract(official)
            if label == off.domain:
                # .bank.in names are short common words (yes.bank.in, axis.bank.in),
                # so yes.com or axis.com are not look-alikes just for sharing them.
                if suffix != off.suffix and off.suffix != "bank.in":
                    return brand_id, official, "wrong_tld"
                continue
            if skeleton(label) == skeleton(off.domain):
                return brand_id, official, "homoglyph"
            # Short names like "sbi" vs "abi" are too easy to hit by accident.
            if len(off.domain) >= 5:
                max_dist = 1 if len(off.domain) < 9 else 2
                if OSA.distance(label, off.domain) <= max_dist:
                    return brand_id, official, "typo"
    return None


def _pick_creation_date(value) -> Optional[datetime]:
    """python-whois returns a datetime, a list of them, a string or None."""
    if isinstance(value, list):
        dates = [v for v in value if isinstance(v, datetime)]
        value = min(dates) if dates else None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _lookup_creation_date(domain: str) -> Optional[datetime]:
    import whois  # python-whois; imported here so tests never need the network

    data = whois.whois(domain, quiet=True, timeout=8)
    return _pick_creation_date(data.get("creation_date"))


def _check_domain_age(domain: str, result: DomainResult) -> None:
    try:
        created = _lookup_creation_date(domain)
    except Exception as e:  # WHOIS servers fail in many ways; never crash
        result.skipped.append(f"Domain age (WHOIS lookup failed: {type(e).__name__})")
        return
    if created is None:
        result.skipped.append("Domain age (WHOIS gave no creation date)")
        return
    days = (datetime.now(timezone.utc) - created).days
    if days < 30:
        result.signals.append(Signal("new_domain", "high", 35,
            f"{domain} was registered only {days} days ago"))
    elif days < 180:
        result.signals.append(Signal("young_domain", "medium", 15,
            f"{domain} was registered {days} days ago (less than 6 months)"))
    else:
        result.signals.append(Signal("domain_age", "info", 0,
            f"{domain} was registered {days // 365} years ago"))


def check_domain(url: str, use_whois: bool = True) -> DomainResult:
    url = url.strip()
    scheme_given = "://" in url
    parts = urlsplit(url if scheme_given else "https://" + url)
    raw_host = (parts.hostname or "").rstrip(".")
    ascii_host = to_ascii(raw_host)
    host = to_unicode(ascii_host)
    registered = get_registered_domain(ascii_host)
    ext = _extract(ascii_host)

    result = DomainResult(url=url, host=host, ascii_host=ascii_host,
                          registered_domain=registered)
    add = result.signals.append

    if not host:
        add(Signal("invalid_url", "high", 50, "The address has no valid host name"))
        return result

    # https://www.sbi.co.in@evil.com really goes to evil.com
    if parts.username is not None:
        hidden_brand = _brand_in_text(parts.username)
        shown = f" ('{brand_name(hidden_brand)}' is only decoration)" if hidden_brand else ""
        result.target_brand = hidden_brand
        add(Signal("userinfo_trick", "high", 45,
            f"The address contains '@'; everything before it is ignored and the real site is {host}{shown}"))

    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
        add(Signal("ip_address", "high", 40,
            f"The address is a bare IP number ({host}) instead of a name"))
    except ValueError:
        pass

    if scheme_given and parts.scheme == "http":
        add(Signal("no_https", "medium", 15,
            "The connection is not encrypted (http instead of https)"))

    official = find_official_brand(ascii_host)
    if official:
        result.official_brand = official
        add(Signal("official_domain", "good", -60,
            f"{registered or host} is an official domain of {brand_name(official)}"))
        return result

    # Readable form for messages, e.g. onlinеsbi.com instead of xn--onlinsbi-g8g.com
    registered_text = to_unicode(registered)

    if host != ascii_host:
        add(Signal("idn_host", "medium", 20,
            f"The address uses non-English look-alike characters ({host} is really {ascii_host})"))

    # Look-alike of an official domain name
    match = None
    if ext.domain and not is_ip:
        match = _find_lookalike(_label_to_unicode(ext.domain), ext.suffix)
    if match:
        brand_id, official_domain, kind = match
        result.target_brand = brand_id
        if kind == "wrong_tld":
            add(Signal("wrong_tld", "high", 40,
                f"{registered_text} has the same name as the official {official_domain} but a different ending"))
        elif kind == "homoglyph":
            add(Signal("homoglyph", "high", 50,
                f"{registered_text} swaps look-alike characters to imitate {official_domain}"))
        else:
            add(Signal("typosquat", "high", 40,
                f"{registered_text} is a misspelling of the official {official_domain}"))

    # Brand name used on a domain the brand doesn't own
    if not match and ext.domain:
        brand_id = _brand_in_text(ext.domain)
        if brand_id:
            result.target_brand = brand_id
            add(Signal("brand_in_domain", "high", 40,
                f"The name '{ext.domain}' contains {brand_name(brand_id)}, "
                f"but {registered} is not one of its official domains"))

    subdomain = ext.subdomain if not is_ip else ""
    sub_brand = _brand_in_text(subdomain) if subdomain else None
    if sub_brand:
        result.target_brand = result.target_brand or sub_brand
        add(Signal("brand_in_subdomain", "high", 40,
            f"'{brand_name(sub_brand)}' appears at the start of the address, "
            f"but the real site is {registered or host}"))

    path_brand = _brand_in_text(parts.path + " " + parts.query)
    if path_brand:
        result.target_brand = result.target_brand or path_brand
        add(Signal("brand_in_path", "medium", 20,
            f"'{brand_name(path_brand)}' appears in the page path, "
            f"but the site is {registered or host}"))

    if ext.suffix and ext.suffix.split(".")[-1] in SUSPICIOUS_TLDS:
        add(Signal("suspicious_tld", "medium", 15,
            f"The ending '.{ext.suffix}' is cheap and often used by fake sites"))

    if use_whois and registered and not is_ip:
        _check_domain_age(registered, result)
    elif not use_whois:
        result.skipped.append("Domain age (WHOIS disabled)")

    return result
