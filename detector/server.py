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
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from detector import llm
from detector.analyze import analyze
from detector.checks.domain import Signal
from detector.explain import LANGUAGES, brand_name_in, explain, fallback_bullets

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Look-Alike Website Detector")


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
    return {"backend": llm.backend_name(), "model": llm.model_name()}


@app.post("/api/check")
def check(req: CheckRequest):
    report = analyze(req.url)
    brand = report.official_brand or report.target_brand
    data = asdict(report)
    data["screenshot"] = ("data:image/png;base64," + base64.b64encode(report.screenshot).decode()
                          if report.screenshot else None)
    data["brand"] = brand
    data["brand_names"] = {lang: brand_name_in(brand, lang) for lang in LANGUAGES}
    seen = report.visual_brand.brand if report.visual_brand else None
    data["visual_brand_names"] = {lang: brand_name_in(seen, lang) for lang in LANGUAGES}
    # Instant explanation in every language, shown until Gemma's version arrives.
    data["quick_explanation"] = {lang: fallback_bullets(report.signals, report.verdict, brand, lang)
                                 for lang in LANGUAGES}
    data["model"] = llm.model_name()
    return data


@app.post("/api/explain")
def explain_endpoint(req: ExplainRequest):
    signals = [Signal(**s.dict()) for s in req.signals]
    bullets, source = explain(signals, req.verdict, req.host, req.brand, req.lang)
    return {"bullets": bullets, "source": source, "model": llm.model_name(), "lang": req.lang}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("detector.server:app", host="127.0.0.1", port=8000)
