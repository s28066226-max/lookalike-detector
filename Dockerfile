# Public website build (Render, or a Hugging Face Docker Space).
# Runs Gemma 4 through the Gemini API; set GEMINI_API_KEY as a secret on the host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PUBLIC_MODE=1 \
    VISION_BACKEND=gemini \
    GEMINI_MODEL=gemma-4-26b-a4b-it \
    PORT=7860

WORKDIR /app
COPY requirements.txt .
# Chromium + its system libraries, installed where the non-root user can read them
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

COPY . .

# Run as a normal user, not root (Hugging Face expects user 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user

EXPOSE 7860
# Render sets PORT itself; Hugging Face uses 7860
# The demo look-alike pages listen on 127.0.0.1 inside the container only:
# the app can check them, but they are never reachable from the internet.
CMD python -m http.server 8001 --bind 127.0.0.1 --directory demo_sites >/dev/null 2>&1 & \
    exec uvicorn detector.server:app --host 0.0.0.0 --port ${PORT:-7860}
