"""Web server: serves the single-page UI and a small JSON API.

Run:  python -m detector.server     (then open http://127.0.0.1:8000)

POST /api/check    {url}                                   -> verdict, signals, screenshot,
                                                              instant fixed-text explanation
POST /api/explain  {host, verdict, brand, signals, lang}   -> Gemma 4 explanation bullets
The explanation is a separate call because Gemma 4 takes ~20 s to write it;
the UI shows the verdict first and swaps in the explanation when it arrives.
Endpoints are plain `def` so FastAPI runs them in worker threads, which the
synchronous Playwright API needs.
"""

import base64
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from detector import llm
from detector.analyze import analyze
from detector.checks.domain import Signal
from detector.checks.page import PUBLIC_MODE
from detector.explain import LANGUAGES, brand_name_in, explain, fallback_bullets, signal_text

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Which of the three checks produced a signal (the UI groups findings this way).
PAGE_CODES = {
    "asks_password", "asks_otp", "asks_card", "asks_identity", "form_posts_elsewhere",
    "form_posts_email", "form_posts_http", "brand_in_page_text", "bad_certificate",
    "http_error", "redirected",
}


def check_of(code: str) -> str:
    if code.startswith("visual_"):
        return "visual"
    return "page" if code in PAGE_CODES else "address"

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Look-Alike Website Detector")

# Limits for the public website, so one visitor can't exhaust the Gemini quota
# or start dozens of browsers. Off on a laptop (PUBLIC_MODE unset).
LIMITS = {"check": (6, 60), "explain": (20, 60)}  # (requests, per seconds) per visitor
_recent = defaultdict(deque)
_lock = threading.Lock()
# Pages opened at the same time; 1 on a 512 MB free server (BROWSER_LIMIT=1)
_browsers = threading.Semaphore(int(os.getenv("BROWSER_LIMIT", "3")))


def rate_limit(request: Request, kind: str) -> None:
    if not PUBLIC_MODE:
        return
    # Behind Hugging Face's proxy the visitor's address is the first X-Forwarded-For entry
    forwarded = request.headers.get("x-forwarded-for", "")
    visitor = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    limit, window = LIMITS[kind]
    now = time.time()
    with _lock:
        hits = _recent[(visitor, kind)]
        while hits and hits[0] < now - window:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests, please wait a minute.")
        hits.append(now)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class CheckRequest(BaseModel):
    url: str


class SignalIn(BaseModel):
    code: str
    severity: str
    points: int
    detail: str


class ExplainRequest(BaseModel):
    host: str
    verdict: str
    brand: Optional[str] = None
    signals: List[SignalIn]
    lang: str = "en"


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"backend": llm.backend_name(), "model": llm.model_name(), "public": PUBLIC_MODE}


@app.post("/api/check")
def check(req: CheckRequest, request: Request):
    rate_limit(request, "check")
    with _browsers:
        report = analyze(req.url[:2000])
    brand = report.official_brand or report.target_brand
    data = asdict(report)
    data["screenshot"] = ("data:image/png;base64," + base64.b64encode(report.screenshot).decode()
                          if report.screenshot else None)
    data["brand"] = brand
    # Each finding as a plain sentence in every language, plus which check found it
    for out, sig in zip(data["signals"], report.signals):
        out["check"] = check_of(sig.code)
        out["plain"] = {lang: signal_text(sig, brand, lang) for lang in LANGUAGES}
    data["brand_names"] = {lang: brand_name_in(brand, lang) for lang in LANGUAGES}
    seen = report.visual_brand.brand if report.visual_brand else None
    data["visual_brand_names"] = {lang: brand_name_in(seen, lang) for lang in LANGUAGES}
    # Instant explanation in every language, shown until Gemma's version arrives.
    data["quick_explanation"] = {lang: fallback_bullets(report.signals, report.verdict, brand, lang)
                                 for lang in LANGUAGES}
    data["model"] = llm.model_name()
    data["backend"] = llm.backend_name()
    return data


@app.post("/api/explain")
def explain_endpoint(req: ExplainRequest, request: Request):
    rate_limit(request, "explain")
    signals = [Signal(**s.dict()) for s in req.signals]
    bullets, source = explain(signals, req.verdict, req.host, req.brand, req.lang)
    return {"bullets": bullets, "source": source, "model": llm.model_name(),
            "backend": llm.backend_name(), "lang": req.lang}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("detector.server:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
