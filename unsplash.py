import logging
import math
import random

import requests
from io import BytesIO
from PIL import Image, ImageOps

from openai_scorer import make_vision_scorer, scorer_config

log = logging.getLogger(__name__)

UNSPLASH_URL = "https://api.unsplash.com/photos/random"
SEARCH_URL = "https://api.unsplash.com/search/photos"
CANDIDATE_POOL = 30
TOP_K = 5
SHORTLIST = 8
MIN_WIDTH = 3000
TARGET_ASPECT = 16 / 9


class UnsplashError(Exception):
    """Raised when Unsplash returns an error or an unexpected payload."""


def build_query(keywords: str) -> str:
    return ",".join(kw.strip() for kw in keywords.split(",") if kw.strip())


def fetch_art_image(access_key: str, keywords: str, size=(3840, 2160),
                    exclude_id=None, rng=random):
    query = build_query(keywords)
    if not query:
        raise ValueError("keywords must contain at least one non-empty term")

    chosen = None
    try:
        candidates = search_photos(access_key, query)
        chosen = choose_photo(candidates, query, exclude_id=exclude_id, rng=rng)
    except UnsplashError as e:
        log.warning("Unsplash search failed (%r); falling back to random", e)

    if chosen is None:
        log.info("random fallback used for q=%r", query)
        chosen = _fetch_random(access_key, query)

    image_url = (chosen.get("urls", {}) or {}).get("full")
    if not image_url:
        raise UnsplashError("Chosen photo has no full-resolution URL")

    # Unsplash requires a download trigger whenever a photo is used — on both the
    # search and random paths. Best-effort.
    trigger_download(access_key, (chosen.get("links", {}) or {}).get("download_location"))
    image = _download_and_resize(image_url, size)
    return image, _photo_meta(chosen)


def _fetch_random(access_key: str, query: str) -> dict:
    meta = requests.get(
        UNSPLASH_URL,
        params={"orientation": "landscape", "query": query},
        headers={"Authorization": f"Client-ID {access_key}"},
        timeout=15,
    )
    if meta.status_code != 200:
        raise UnsplashError(f"Unsplash returned {meta.status_code}: {meta.text[:200]}")
    data = meta.json()
    if not isinstance(data, dict) or "urls" not in data:
        raise UnsplashError(f"Unexpected Unsplash response: {str(data)[:200]}")
    return data


def search_photos(access_key: str, query: str, per_page: int = CANDIDATE_POOL) -> list:
    resp = requests.get(
        SEARCH_URL,
        params={
            "query": query,
            "orientation": "landscape",
            "content_filter": "high",
            "order_by": "relevant",
            "per_page": per_page,
        },
        headers={"Authorization": f"Client-ID {access_key}"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise UnsplashError(f"Unsplash search returned {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    results = data.get("results", []) if isinstance(data, dict) else []
    for i, c in enumerate(results):
        c["_rank"] = i
    log.info("search q=%r status=%d results=%d", query, resp.status_code, len(results))
    return results


def _meets_min(c: dict) -> bool:
    w, h = c.get("width", 0) or 0, c.get("height", 0) or 0
    return w >= MIN_WIDTH and w >= h


def heuristic_scorer(c: dict) -> float:
    rank = c.get("_rank", 0) or 0
    likes = c.get("likes", 0) or 0
    w, h = c.get("width", 0) or 0, c.get("height", 0) or 0
    rank_score = 1.0 / (1 + rank)
    likes_score = math.log1p(likes)
    aspect = (w / h) if h else 0
    aspect_penalty = abs(aspect - TARGET_ASPECT)
    return rank_score * 2.0 + likes_score * 0.5 - aspect_penalty * 1.0


def rank_candidates(candidates, scorer=heuristic_scorer):
    """Filter to min-resolution-eligible candidates and return
    [(candidate, score)] sorted by score descending. Scorer is called exactly
    once per candidate (important when it is an expensive vision call)."""
    eligible = [c for c in (candidates or []) if _meets_min(c)]
    ranked = [(c, scorer(c)) for c in eligible]
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def pick_from_ranked(ranked, *, exclude_id=None, top_k=TOP_K, rng=random):
    """Choose among the top_k highest-scored, avoiding exclude_id when possible."""
    if not ranked:
        return None
    top = [c for c, _ in ranked[:top_k]]
    pool = [c for c in top if c.get("id") != exclude_id] or top
    return rng.choice(pool)


def select_best(candidates, exclude_id=None, scorer=heuristic_scorer, top_k=TOP_K, rng=random):
    return pick_from_ranked(rank_candidates(candidates, scorer),
                            exclude_id=exclude_id, top_k=top_k, rng=rng)


def _photo_meta(c: dict) -> dict:
    user = c.get("user", {}) or {}
    links = c.get("links", {}) or {}
    return {
        "id": c.get("id"),
        "description": c.get("description") or c.get("alt_description"),
        "photographer": user.get("name"),
        "source_url": links.get("html"),
    }


def trigger_download(access_key: str, download_location) -> None:
    """Unsplash requires a GET to download_location whenever a photo is used.
    Best-effort: never break the art update over an attribution ping."""
    if not download_location:
        return
    try:
        requests.get(download_location, headers={"Authorization": f"Client-ID {access_key}"}, timeout=10)
    except Exception as e:  # noqa: BLE001
        log.warning("Unsplash download trigger failed: %r", e)


def _download_and_resize(image_url: str, size) -> bytes:
    photo = requests.get(f"{image_url}&w={size[0]}&h={size[1]}", timeout=30)
    if photo.status_code != 200:
        raise UnsplashError(f"Image download failed: {photo.status_code}")
    img = ImageOps.fit(Image.open(BytesIO(photo.content)), size, Image.LANCZOS).convert("RGB")
    out = BytesIO()
    img.save(out, format="JPEG", optimize=True, quality=90)
    return out.getvalue()


def choose_photo(candidates, destination, *, exclude_id=None, rng=random, config=None):
    """Two-stage pick: heuristic pre-filter to a shortlist, then score by the
    configured backend (vision when set, else heuristic). Logs each funnel stage."""
    eligible = [c for c in (candidates or []) if _meets_min(c)]
    if not eligible:
        log.info("shortlist empty (no candidate meets min-res gate)")
        return None
    eligible.sort(key=heuristic_scorer, reverse=True)
    shortlist = eligible[:SHORTLIST]
    dropped = len(eligible) - len(shortlist)

    cfg = config if config is not None else scorer_config()
    if cfg.get("backend") == "openai" and cfg.get("url") and cfg.get("model"):
        scorer = make_vision_scorer(destination, cfg)
        backend = "openai"
    else:
        scorer = heuristic_scorer
        backend = "heuristic"

    # rank_candidates scores each shortlisted photo once; pick_from_ranked then
    # narrows to TOP_K and rotates among them (do NOT widen top_k to SHORTLIST,
    # or the score stops affecting the pick).
    ranked = rank_candidates(shortlist, scorer)
    scored = ", ".join(f"{c.get('id')} score={s:.2f}" for c, s in ranked)
    tail = f"  dropped {dropped} beyond shortlist" if dropped else ""
    log.info("shortlist(%d) backend=%s: %s%s", len(shortlist), backend, scored, tail)

    chosen = pick_from_ranked(ranked, exclude_id=exclude_id, top_k=TOP_K, rng=rng)
    if chosen is not None:
        cs = next((s for c, s in ranked if c is chosen), None)
        log.info("picked %s score=%s (top%d, excluded_last=%s) [backend=%s]",
                 chosen.get("id"), f"{cs:.2f}" if cs is not None else "?", TOP_K, exclude_id, backend)
    return chosen
