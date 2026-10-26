# Look-Alike Website Detector

Hackathon project (Hacktoberfest Hack Day, problem statement #10). It has 12 hours, started from an empty repo, and is judged on a live demo.
**Track: Main Track, Best Open-Source AI Project.** Open-weight AI (Gemma 4) must be an important part of how it works, and the repo must be public with an open-source license.

## Problem
Fake websites copy the look of Indian banks and government portals. People notice the wrong web address only after it's too late.
**Goal:** given a URL, tell the user whether the site is genuine or a look-alike, and explain the warning signs in plain words (English + Hindi/Marathi).

## Architecture
URL → three independent checks → combined verdict → plain-language explanation.

1. **Address check** (deterministic, no AI): `checks/domain.py`
   - Compare the registered domain to `data/brands.json` (brand → official domains, display names, logo keywords)
   - Edit distance to official domains (rapidfuzz), homoglyph / IDN / punycode detection (sbl vs sbi, Cyrillic letters)
   - Brand name in a subdomain or path of a different domain (`sbi.secure-login.xyz`)
   - Domain age via WHOIS (treat failure as "unknown", never as a crash)
   - Suspicious TLD, no HTTPS, IP-address hosts
2. **Page check** (deterministic): `checks/page.py`
   - Fetch the page with Playwright: take a screenshot and grab the rendered HTML
   - Password/OTP/card fields present? Where do forms POST to (a different domain = red flag)?
3. **Visual brand check** (AI): `checks/vision.py`
   - Send the screenshot to a vision model and ask for strict JSON: `{"brand": str|null, "confidence": 0-1, "evidence": [str]}`
   - Red flag: the brand is detected AND the domain is not in that brand's official list.
4. **Verdict**: `verdict.py`
   - Rule-based score from the signals above → `genuine | suspicious | likely_fake`.
   - **The model never decides the verdict on its own.** It only supplies brand recognition and the explanation text.
5. **Explanation** (AI): turn the list of triggered signals into 3–5 short bullets that a non-technical user understands, in the chosen language. Use only the signals passed in; never invent new ones.

## Model backends
Both backends run **Gemma 4, an open-weight model**. One switch, `VISION_BACKEND` in `.env`:
- `gemini` (default): Gemma 4 through the Gemini API. Key in `GEMINI_API_KEY`, model id in `GEMINI_MODEL`. Fast to build with, and nothing to download.
- `ollama` (optional, local): Gemma 4 open weights through Ollama. Model name in `OLLAMA_MODEL`. Used in the demo to show it runs fully offline with no data leaving the machine, which supports the open-source case for the Main Track.
Both backends implement the same `describe_brand(image_bytes) -> BrandResult` and `explain(signals, lang) -> list[str]`. Never hard-code keys. `.env` is gitignored, and `.env.example` is committed.

## Stack
- Python 3.12 if installed, otherwise the system Python 3.9. **Write 3.9-compatible code** (use `Optional[str]`, not `str | None`). FastAPI, Playwright (chromium), rapidfuzz, tldextract, python-whois, beautifulsoup4, httpx
- Frontend: a single static `web/index.html` + vanilla JS served by FastAPI (URL box, verdict badge, signal list, screenshot thumbnail, language toggle)
- Tests: pytest for the domain checks (they're deterministic, so they're easy to test)

## Demo pages
`demo_sites/` holds fake look-alike pages served **only on localhost** (e.g. `python -m http.server 8001`), for example a fake "SBI NetBanking" login page whose form posts elsewhere. Never fetch or ship real phishing sites. Add a visible "DEMO – NOT A REAL BANK" footer to each.

## Build order (deadline 2:30 PM, about 4 hours total; have something working at every stage)
1. **By 11:30:** address check + CLI (`python -m detector <url>`) + pytest
2. **By 12:30:** Playwright screenshot + page check + vision brand check (Gemma 4 through the Gemini API) + combined verdict
3. **By 1:30:** FastAPI + single-page web UI + plain-language explanation (English/Hindi) + demo look-alike pages
4. **By 2:15:** README, screenshots, push, submit on OrganizerHQ
5. Skip unless everything else is done: the local Ollama backend
Keep the scope small. A working, polished core beats unfinished extras.

## Rules to respect
- Repo is public with a LICENSE (MIT). Keep it that way.
- The README must name every model, tool and library **with its license**, and include an "AI tools used" section (Claude Code).
- The team must understand all code: prefer simple, readable code over clever code, and add short comments where logic isn't obvious.
- Fail gracefully: if WHOIS, Playwright or the model fails, still return a verdict from the remaining signals and say which check was skipped.
