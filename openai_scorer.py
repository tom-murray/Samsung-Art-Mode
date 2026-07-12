"""Score candidate images with any OpenAI-compatible /chat/completions endpoint
(LM Studio, Ollama /v1, vLLM, LocalAI, OpenAI). No dependency on unsplash."""
import base64
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)

PROMPT = (
    "You are curating art for a Samsung Frame TV. Rate the image as framed wall "
    "art of {destination} from 0 to 10. Score high only if it clearly depicts "
    "{destination}, looks like a professional, well-composed scenic photograph "
    "suitable for wall art, and contains no people, faces, text, logos or "
    'watermarks. Reply with ONLY JSON: {{"score": <number 0-10>, '
    '"reason": "<8 words max>"}}.'
)


def scorer_config() -> dict:
    return {
        "backend": os.environ.get("SCORER_BACKEND", "heuristic"),
        "url": os.environ.get("SCORER_URL"),
        "model": os.environ.get("SCORER_MODEL"),
        "api_key": os.environ.get("SCORER_API_KEY"),
        "timeout": float(os.environ.get("SCORER_TIMEOUT", "30")),
    }


def _parse_result(content):
    """Return (score, reason). Falls back to the first number and no reason."""
    if not content:
        return None, None
    try:
        obj = json.loads(content)
        score = float(obj["score"])
        reason = obj.get("reason")
        return score, (str(reason) if reason is not None else None)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    m = re.search(r"-?\d+(?:\.\d+)?", content)
    return (float(m.group()) if m else None), None


def score_image(image_bytes, destination, *, url, model, api_key=None, timeout=30):
    data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1000,
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
    log.debug("LLM raw response: %r", content)  # never log the base64 image
    return _parse_result(content)


def _fetch_small(candidate) -> bytes:
    urls = candidate.get("urls", {}) or {}
    src = urls.get("small") or urls.get("regular") or urls.get("full")
    resp = requests.get(src, timeout=20)
    resp.raise_for_status()
    return resp.content


def make_vision_scorer(destination, config):
    """Return scorer(candidate)->float. On any error, a neutral 5.0 keeps the
    0-10 scale consistent (a failed call neither wins nor loses). Each call is
    logged with the image's score, short reason, and elapsed time."""
    def scorer(candidate):
        t0 = time.monotonic()
        try:
            img = _fetch_small(candidate)
            s, reason = score_image(img, destination, url=config["url"], model=config["model"],
                                    api_key=config.get("api_key"), timeout=config.get("timeout", 30))
            ms = int((time.monotonic() - t0) * 1000)
            if s is not None:
                log.info("vision %s score=%.1f reason=%r (%dms)", candidate.get("id"), s, reason, ms)
                return s
            log.info("vision %s no score parsed → neutral 5.0 (%dms)", candidate.get("id"), ms)
        except Exception as e:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            log.warning("vision %s FAILED (%r) → neutral 5.0 (%dms)", candidate.get("id"), e, ms)
        return 5.0
    return scorer
