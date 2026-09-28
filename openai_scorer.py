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
    "You are curating gallery-quality art for a Samsung Frame TV showing "
    "{destination}. Rate the image from 0 to 10 as framed wall art of "
    "{destination}. Reward instantly-recognisable, striking views of "
    "{destination}: famous landmarks and points of interest, city skylines and "
    "cityscapes, sweeping aerial or drone shots, heritage and historic sites, "
    "and natural or built attractions — professional, well-composed, and clearly "
    "set in {destination}. Score LOW (0-4) for images not clearly {destination} "
    "or that could be anywhere, people or portraits as the subject, food, "
    "interiors, close-ups of objects, signage, text, logos, watermarks, "
    "snapshots, and dull or cluttered composition. Use the FULL range and be "
    "critical: most images are average (4-6); reserve 9-10 for truly "
    "exceptional, iconic wall-art shots. Reply with ONLY JSON: "
    '{{"score": <number 0-10>, "reason": "<8 words max>"}}.'
)


def scorer_config() -> dict:
    return {
        "backend": os.environ.get("SCORER_BACKEND", "heuristic"),
        "url": os.environ.get("SCORER_URL"),
        "model": os.environ.get("SCORER_MODEL"),
        "api_key": os.environ.get("SCORER_API_KEY"),
        "timeout": float(os.environ.get("SCORER_TIMEOUT", "30")),
        "max_tokens": int(os.environ.get("SCORER_MAX_TOKENS", "1000")),
    }


# Reasoning vision models (e.g. GLM) wrap their JSON answer in special box tokens
# or markdown fences. Strip those, then pull the first {...} object out and parse
# it so the reason survives (not just the bare score).
_BOX_TOKENS = re.compile(r"<\|begin_of_box\|>|<\|end_of_box\|>")


def _extract_json(text):
    """Parse the first {...} JSON object out of `text`, tolerating model box
    tokens and markdown fences. Returns (None, None) if none is found/valid."""
    if not text:
        return None, None
    cleaned = _BOX_TOKENS.sub("", text)
    m = re.search(r"\{.*?\}", cleaned, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            score = float(obj["score"])
            reason = obj.get("reason")
            return score, (str(reason) if reason is not None else None)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    return None, None


def _parse_result(content):
    """Return (score, reason) from a model's answer text: prefer a JSON object,
    otherwise fall back to the first bare number (a model may reply e.g. '8/10')."""
    score, reason = _extract_json(content)
    if score is not None:
        return score, reason
    m = re.search(r"-?\d+(?:\.\d+)?", _BOX_TOKENS.sub("", content or ""))
    return (float(m.group()) if m else None), None


def score_image(image_bytes, destination, *, url, model, api_key=None, timeout=30,
                max_tokens=1000):
    """Return (score, reason, finish_reason). max_tokens must be generous enough
    for reasoning models to finish thinking AND emit the JSON answer."""
    data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
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
    choice = resp.json()["choices"][0]
    message = choice.get("message", {}) or {}
    finish = choice.get("finish_reason")
    content = message.get("content") or ""
    log.debug("LLM raw response: %r (finish=%s)", content, finish)  # never log the base64 image
    score, reason = _parse_result(content)
    if score is None:
        # Some reasoning models leave content empty and keep the answer in
        # reasoning_content. Accept a real JSON object there, but NEVER a bare
        # number — truncated chain-of-thought is full of incidental numbers and
        # would yield a confident bogus score instead of the neutral fallback.
        score, reason = _extract_json(message.get("reasoning_content") or "")
    return score, reason, finish


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
            s, reason, finish = score_image(
                img, destination, url=config["url"], model=config["model"],
                api_key=config.get("api_key"), timeout=config.get("timeout", 30),
                max_tokens=config.get("max_tokens", 1000))
            ms = int((time.monotonic() - t0) * 1000)
            if s is not None:
                log.info("vision %s score=%.1f reason=%r finish=%s (%dms)",
                         candidate.get("id"), s, reason, finish, ms)
                return s
            log.info("vision %s no score parsed (finish=%s) → neutral 5.0 (%dms)",
                     candidate.get("id"), finish, ms)
        except Exception as e:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            log.warning("vision %s FAILED (%r) → neutral 5.0 (%dms)", candidate.get("id"), e, ms)
        return 5.0
    return scorer
