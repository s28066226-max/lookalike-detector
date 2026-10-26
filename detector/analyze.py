"""Run all checks on a URL and combine them into one report.

Address check and page fetch run at the same time; the vision check needs the
screenshot, so it runs after. Any check can fail: it is listed in `skipped`
and the verdict comes from the signals that remain.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

from detector import llm
from detector.checks.domain import Signal, check_domain
from detector.checks.page import PageResult, check_page
from detector.checks.vision import BrandResult, brand_signals, describe_brand
from detector.verdict import risk_score, verdict_for


@dataclass
class Report:
    url: str
    host: str
    registered_domain: str
    official_brand: Optional[str]
    target_brand: Optional[str]
    final_url: str = ""
    page_title: str = ""
    visual_brand: Optional[BrandResult] = None
    signals: List[Signal] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    score: int = 0
    verdict: str = "genuine"
    screenshot: Optional[bytes] = None


def analyze(url: str, use_whois: bool = True, use_page: bool = True, use_vision: bool = True) -> Report:
    with ThreadPoolExecutor(max_workers=2) as pool:
        domain_job = pool.submit(check_domain, url, use_whois)
        page_job = pool.submit(check_page, url) if use_page else None
        dom = domain_job.result()
        page = page_job.result() if page_job else PageResult(skipped=["Page check (disabled)"])

    report = Report(url=url, host=dom.host, registered_domain=dom.registered_domain,
                    official_brand=dom.official_brand, target_brand=dom.target_brand,
                    final_url=page.final_url, page_title=page.title,
                    screenshot=page.screenshot)
    report.signals = dom.signals + page.signals
    report.skipped = dom.skipped + page.skipped

    # If the page redirected to another site, that site's address matters too.
    judged = dom
    if page.final_url and any(s.code == "redirected" for s in page.signals):
        judged = check_domain(page.final_url, use_whois)
        seen = {s.code for s in report.signals}
        for s in judged.signals:
            if s.code not in seen:
                report.signals.append(Signal(s.code, s.severity, s.points, "After redirect: " + s.detail))
        report.target_brand = report.target_brand or judged.target_brand

    if not use_vision:
        report.skipped.append("Visual brand check (disabled)")
    elif not page.screenshot:
        report.skipped.append("Visual brand check (no screenshot)")
    elif page.http_status and page.http_status >= 400:
        report.skipped.append(f"Visual brand check (page was an HTTP {page.http_status} error page)")
    else:
        try:
            report.visual_brand = describe_brand(page.screenshot)
            report.signals += brand_signals(report.visual_brand, judged.ascii_host)
            report.target_brand = report.target_brand or report.visual_brand.brand
        except llm.ModelUnavailable as e:
            report.skipped.append(f"Visual brand check ({e})")
        except Exception as e:  # network error, bad JSON, quota...
            report.skipped.append(f"Visual brand check (model error: {type(e).__name__}: {str(e)[:100]})")

    report.score = risk_score(report.signals)
    report.verdict = verdict_for(report.score)
    return report
