"""Command line: python -m detector <url> [--no-whois] [--no-page] [--no-vision] [--json]"""

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from detector import llm
from detector.analyze import analyze
from detector.checks.domain import brand_name

SEVERITY_LABEL = {"high": "HIGH", "medium": "MED", "low": "LOW", "info": "INFO", "good": "OK"}
SCREENSHOT_DIR = Path("screenshots")


def save_screenshot(png: bytes, host: str) -> Path:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = SCREENSHOT_DIR / (re.sub(r"[^\w.-]+", "_", host or "page") + ".png")
    path.write_bytes(png)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m detector",
                                     description="Check whether a URL is a genuine site or a look-alike.")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--no-whois", action="store_true", help="skip the domain-age lookup")
    parser.add_argument("--no-page", action="store_true", help="don't open the page (address check only)")
    parser.add_argument("--no-vision", action="store_true", help="skip the Gemma 4 visual brand check")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--list-models", action="store_true", help="list Gemma models your Gemini key can use")
    args = parser.parse_args()

    if args.list_models:
        print("\n".join(llm.list_gemma_models()))
        return
    if not args.url:
        parser.error("a URL is required")

    report = analyze(args.url, use_whois=not args.no_whois,
                     use_page=not args.no_page, use_vision=not args.no_vision)
    shot_path = save_screenshot(report.screenshot, report.host) if report.screenshot else None

    if args.json:
        data = asdict(report)
        data["screenshot"] = str(shot_path) if shot_path else None
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print(f"URL:      {report.url}")
    print(f"Host:     {report.host}")
    if report.registered_domain:
        print(f"Domain:   {report.registered_domain}")
    if report.final_url and report.final_url.rstrip("/") != report.url.rstrip("/"):
        print(f"Ended at: {report.final_url}")
    if report.page_title:
        print(f"Title:    {report.page_title}")
    if report.official_brand:
        print(f"Official: {brand_name(report.official_brand)}")
    elif report.target_brand:
        print(f"Imitates: {brand_name(report.target_brand)}")
    if report.visual_brand:
        vb = report.visual_brand
        seen = brand_name(vb.brand) if vb.brand else "no known brand"
        print(f"Gemma 4:  {seen} (confidence {vb.confidence:.2f}, model {llm.model_name()})")
    if shot_path:
        print(f"Screenshot: {shot_path}")

    print("\nSignals:")
    if not report.signals:
        print("  (none)")
    for s in report.signals:
        print(f"  [{SEVERITY_LABEL[s.severity]:<4} {s.points:+4d}] {s.code:<22} {s.detail}")
    for skipped in report.skipped:
        print(f"  [SKIPPED  ] {skipped}")

    print(f"\nRisk score: {report.score}/100  ->  {report.verdict.upper().replace('_', ' ')}")


if __name__ == "__main__":
    try:
        main()
    except llm.ModelUnavailable as e:
        sys.exit(f"Model not available: {e}")
