"""One place that talks to the model. Both backends run Gemma 4 (open weights).

VISION_BACKEND in .env picks the backend:
  gemini  Gemma 4 through the Gemini API (GEMINI_API_KEY, GEMINI_MODEL)
  ollama  Gemma 4 running locally through Ollama (OLLAMA_MODEL): works offline,
          nothing leaves the machine
"""

import base64
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")


class ModelUnavailable(Exception):
    """The model can't be used (no key, unknown backend, ...). Callers skip the AI step."""


def backend_name() -> str:
    return os.getenv("VISION_BACKEND", "gemini").strip().lower()


def model_name() -> str:
    if backend_name() == "ollama":
        return os.getenv("OLLAMA_MODEL", "gemma4:e4b")
    return os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it")


def generate(prompt: str, image_png: Optional[bytes] = None, timeout_s: int = 60) -> str:
    """Send a prompt (plus optional screenshot) and return the model's text reply."""
    backend = backend_name()
    if backend == "gemini":
        return _generate_gemini(prompt, image_png, timeout_s)
    if backend == "ollama":
        return _generate_ollama(prompt, image_png, timeout_s)
    raise ModelUnavailable(f"backend '{backend}' is not available")


def _generate_gemini(prompt: str, image_png: Optional[bytes], timeout_s: int) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ModelUnavailable("GEMINI_API_KEY is not set in .env")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_s * 1000))
    contents = []
    if image_png:
        contents.append(types.Part.from_bytes(data=image_png, mime_type="image/png"))
    contents.append(prompt)
    # Gemma models don't support system instructions or JSON mode on this API,
    # so everything goes in the prompt and we parse the JSON out of the text.
    from google.genai.errors import ServerError

    for attempt in range(2):  # one retry: the API sometimes returns a random 500
        try:
            response = client.models.generate_content(
                model=model_name(),
                contents=contents,
                config=types.GenerateContentConfig(temperature=0),
            )
            return response.text or ""
        except ServerError:
            if attempt == 1:
                raise
    return ""


def _generate_ollama(prompt: str, image_png: Optional[bytes], timeout_s: int) -> str:
    import httpx

    model = model_name()
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,  # skip Gemma 4's reasoning step: much faster, same answers for our tasks
        "options": {"temperature": 0},
        "keep_alive": "30m",  # keep the model in memory so the next check is fast
    }
    if image_png:
        body["images"] = [base64.b64encode(image_png).decode("ascii")]
    try:
        response = httpx.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=timeout_s)
        if response.status_code == 400 and "think" in response.text:
            # Older Ollama versions don't know the "think" option
            body.pop("think")
            response = httpx.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=timeout_s)
    except httpx.ConnectError:
        raise ModelUnavailable("Ollama is not running (open the Ollama app)")
    if response.status_code == 404:
        raise ModelUnavailable(f"model '{model}' is not downloaded (run: ollama pull {model})")
    response.raise_for_status()
    return response.json().get("response", "")


def list_gemma_models():
    """Gemma models the current backend can use (to fill GEMINI_MODEL / OLLAMA_MODEL)."""
    if backend_name() == "ollama":
        import httpx

        try:
            tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
        except httpx.ConnectError:
            raise ModelUnavailable("Ollama is not running (open the Ollama app)")
        return sorted(m["name"] for m in tags.get("models", []) if "gemma" in m["name"])
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ModelUnavailable("GEMINI_API_KEY is not set in .env")
    from google import genai

    client = genai.Client(api_key=api_key)
    return sorted(m.name.replace("models/", "") for m in client.models.list() if "gemma" in m.name)
