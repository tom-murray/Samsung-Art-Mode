import unsplash


def test_build_query_trims_and_drops_empties():
    assert unsplash.build_query(" Dubai , cityscape ,, ") == "Dubai,cityscape"


def test_build_query_empty_returns_empty_string():
    assert unsplash.build_query("  ,, ") == ""


from io import BytesIO
from unittest.mock import patch
from PIL import Image
import unsplash


def _png_bytes(size=(64, 32)):
    buf = BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = ""

    def json(self):
        return self._json


def test_fetch_art_image_returns_jpeg_bytes_at_target_size():
    meta = _Resp(200, {"urls": {"full": "https://img.example/full"}})
    photo = _Resp(200, content=_png_bytes())
    with patch("unsplash.requests.get", side_effect=[meta, photo]):
        data = unsplash.fetch_art_image("key", "Dubai", size=(320, 180))
    img = Image.open(BytesIO(data))
    assert img.format == "JPEG"
    assert img.size == (320, 180)


def test_fetch_art_image_raises_on_non_200_meta():
    with patch("unsplash.requests.get", side_effect=[_Resp(403, {})]):
        try:
            unsplash.fetch_art_image("key", "Dubai")
            assert False, "expected UnsplashError"
        except unsplash.UnsplashError:
            pass


def test_fetch_art_image_raises_on_missing_urls():
    with patch("unsplash.requests.get", side_effect=[_Resp(200, {"errors": ["bad"]})]):
        try:
            unsplash.fetch_art_image("key", "Dubai")
            assert False, "expected UnsplashError"
        except unsplash.UnsplashError:
            pass


def test_fetch_art_image_raises_on_empty_keywords():
    try:
        unsplash.fetch_art_image("key", " , ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_search_photos_returns_ranked_results():
    payload = {"results": [{"id": "a"}, {"id": "b"}]}
    with patch("unsplash.requests.get", return_value=_Resp(200, payload)):
        results = unsplash.search_photos("key", "Kyoto")
    assert [c["id"] for c in results] == ["a", "b"]
    assert results[0]["_rank"] == 0 and results[1]["_rank"] == 1


def test_search_photos_raises_on_non_200():
    with patch("unsplash.requests.get", return_value=_Resp(403, {})):
        try:
            unsplash.search_photos("key", "Kyoto")
            assert False, "expected UnsplashError"
        except unsplash.UnsplashError:
            pass
