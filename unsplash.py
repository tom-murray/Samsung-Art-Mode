import requests
from io import BytesIO
from PIL import Image, ImageOps

UNSPLASH_URL = "https://api.unsplash.com/photos/random"


class UnsplashError(Exception):
    """Raised when Unsplash returns an error or an unexpected payload."""


def build_query(keywords: str) -> str:
    return ",".join(kw.strip() for kw in keywords.split(",") if kw.strip())


def fetch_art_image(access_key: str, keywords: str, size=(3840, 2160)) -> bytes:
    query = build_query(keywords)
    if not query:
        raise ValueError("keywords must contain at least one non-empty term")

    meta = requests.get(
        UNSPLASH_URL,
        params={"orientation": "landscape", "query": query},
        headers={"Authorization": f"Client-ID {access_key}"},
        timeout=15,
    )
    if meta.status_code != 200:
        raise UnsplashError(f"Unsplash returned {meta.status_code}: {meta.text[:200]}")
    data = meta.json()
    try:
        image_url = data["urls"]["full"]
    except (KeyError, TypeError):
        raise UnsplashError(f"Unexpected Unsplash response: {str(data)[:200]}")

    photo = requests.get(f"{image_url}&w={size[0]}&h={size[1]}", timeout=30)
    if photo.status_code != 200:
        raise UnsplashError(f"Image download failed: {photo.status_code}")

    img = ImageOps.fit(Image.open(BytesIO(photo.content)), size, Image.LANCZOS).convert("RGB")
    out = BytesIO()
    img.save(out, format="JPEG", optimize=True, quality=90)
    return out.getvalue()
