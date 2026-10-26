"""Deploy the public website to a Hugging Face Space.

Usage (after `hf auth login`, or with HF_TOKEN=... in .env):
    python deploy/deploy_hf_space.py <hf-username>/lookalike-detector

It creates the Space (Docker) if needed, uploads the project files that git
tracks or would track (so .env, .venv and screenshots are never uploaded),
and stores GEMINI_API_KEY from .env as a private Space secret.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent

# Hugging Face reads the Space settings from the front matter of README.md
SPACE_README = """---
title: Look-Alike Website Detector
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
license: mit
short_description: Spot fake Indian bank & govt websites with Gemma 4
---

# Look-Alike Website Detector

Paste a link and find out if it is the real Indian bank or government website or a
look-alike, with the warning signs explained in 7 Indian languages. Runs on
**Gemma 4** (open weights, Apache 2.0) through the Gemini API.

Source code, documentation and licenses: {github}
"""


def project_files():
    """Files git tracks plus new files git doesn't ignore."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if (ROOT / f).is_file()]


def main():
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        sys.exit("usage: python deploy/deploy_hf_space.py <hf-username>/<space-name>")
    repo_id = sys.argv[1]
    env = dotenv_values(ROOT / ".env")
    # Token from .env if present, otherwise the one saved by `hf auth login`
    api = HfApi(token=(env.get("HF_TOKEN") or "").strip() or None)

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        for f in project_files():
            if f in (".env",) or f.startswith(".vscode/"):
                continue  # never upload secrets or editor settings
            target = Path(tmp) / f
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, target)
        github = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip().removesuffix(".git")
        (Path(tmp) / "README.md").write_text(SPACE_README.format(github=github), encoding="utf-8")
        api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=tmp,
                          commit_message="Deploy Look-Alike Website Detector")

    key = (env.get("GEMINI_API_KEY") or "").strip()
    if key:
        api.add_space_secret(repo_id, "GEMINI_API_KEY", key)
        print("Stored GEMINI_API_KEY as a private Space secret.")
    else:
        print("No GEMINI_API_KEY in .env: add it in the Space settings, or the AI checks will be skipped.")

    print(f"Deployed. Build log and app: https://huggingface.co/spaces/{repo_id}")


if __name__ == "__main__":
    main()
