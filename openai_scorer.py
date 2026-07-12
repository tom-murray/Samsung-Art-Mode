"""Score candidate images with any OpenAI-compatible /chat/completions endpoint
(LM Studio, Ollama /v1, vLLM, LocalAI, OpenAI). No dependency on unsplash."""
import base64
import json
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

PROMPT = (
    "You are curating art for a Samsung Frame TV. Rate the image as framed wall "
    "art of {destination} from 0 to 10. Score high only if it clearly depicts "
    "{destination}, looks like a professional, well-composed scenic photograph "
    "suitable for wall art, and contains no people, faces, text, logos or "
    'watermarks. Reply with ONLY JSON: {{"score": <number 0-10>}}.'
)


def scorer_config() -> dict:
    return {
        "backend": os.environ.get("SCORER_BACKEND", "heuristic"),
        "url": os.environ.get("SCORER_URL"),
        "model": os.environ.get("SCORER_MODEL"),
        "api_key": os.environ.get("SCORER_API_KEY"),
        "timeout": float(os.environ.get("SCORER_TIMEOUT", "30")),
    }


def _parse_score(content):
    if not content:
        return None
    try:
        return float(json.loads(content)["score"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    m = re.search(r"-?\d+(?:\.\d+)?", content)
    return float(m.group()) if m else None


def score_image(image_bytes, destination, *, url, model, api_key=None, timeout=30):
    data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(destination=destination)},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(url.rstrip("/") + "/chat/completions", json=payload,
                         headers=headers, timeout=timeout)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_score(content)


def _fetch_small(candidate) -> bytes:
    urls = candidate.get("urls", {}) or {}
    src = urls.get("small") or urls.get("regular") or urls.get("full")
    resp = requests.get(src, timeout=20)
    resp.raise_for_status()
    return resp.content


def make_vision_scorer(destination, config):
    """Return scorer(candidate)->float. On any error, a neutral 5.0 keeps the
    0-10 scale consistent (a failed call neither wins nor loses)."""
    def scorer(candidate):
        try:
            img = _fetch_small(candidate)
            s = score_image(img, destination, url=config["url"], model=config["model"],
                            api_key=config.get("api_key"), timeout=config.get("timeout", 30))
            if s is not None:
                return s
        except Exception as e:  # noqa: BLE001
            log.warning("vision score failed for %s (%r)", candidate.get("id"), e)
        return 5.0
    return scorer
