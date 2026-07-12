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


def test_heuristic_scorer_prefers_better_photo():
    good = {"_rank": 0, "likes": 500, "width": 5000, "height": 2813}
    poor = {"_rank": 8, "likes": 3, "width": 4000, "height": 3000}
    assert unsplash.heuristic_scorer(good) > unsplash.heuristic_scorer(poor)


def test_meets_min_rejects_low_res_and_portrait():
    assert unsplash._meets_min({"width": 5000, "height": 2813}) is True
    assert unsplash._meets_min({"width": 2000, "height": 1200}) is False
    assert unsplash._meets_min({"width": 3000, "height": 4000}) is False


class _FirstRng:
    """Deterministic stand-in for the random module: always the first item."""
    @staticmethod
    def choice(seq):
        return seq[0]


def _cand(id, rank, likes=100, width=5000, height=2813):
    return {"id": id, "_rank": rank, "likes": likes, "width": width, "height": height}


def test_select_best_picks_highest_scored():
    cands = [_cand("a", 3, likes=1), _cand("b", 0, likes=999)]
    chosen = unsplash.select_best(cands, rng=_FirstRng)
    assert chosen["id"] == "b"


def test_select_best_excludes_last_shown():
    cands = [_cand("b", 0, likes=999), _cand("c", 1, likes=800)]
    chosen = unsplash.select_best(cands, exclude_id="b", rng=_FirstRng)
    assert chosen["id"] == "c"


def test_select_best_returns_none_when_all_below_min_res():
    cands = [{"id": "x", "_rank": 0, "likes": 5, "width": 1000, "height": 600}]
    assert unsplash.select_best(cands, rng=_FirstRng) is None


def test_select_best_falls_back_to_best_when_only_option_is_excluded():
    cands = [_cand("b", 0, likes=999)]
    chosen = unsplash.select_best(cands, exclude_id="b", rng=_FirstRng)
    assert chosen["id"] == "b"


def test_photo_meta_extracts_attribution():
    c = {"id": "p1", "alt_description": "kyoto dusk",
         "user": {"name": "Jane"}, "links": {"html": "https://unsplash.com/photos/p1"}}
    meta = unsplash._photo_meta(c)
    assert meta == {"id": "p1", "description": "kyoto dusk",
                    "photographer": "Jane", "source_url": "https://unsplash.com/photos/p1"}


def test_trigger_download_is_best_effort():
    with patch("unsplash.requests.get", side_effect=RuntimeError("boom")):
        unsplash.trigger_download("key", "https://api.unsplash.com/photos/p1/download")


def test_download_and_resize_returns_target_jpeg():
    photo = _Resp(200, content=_png_bytes())
    with patch("unsplash.requests.get", return_value=photo):
        data = unsplash._download_and_resize("https://img.example/full", (320, 180))
    img = Image.open(BytesIO(data))
    assert img.format == "JPEG" and img.size == (320, 180)


def test_choose_photo_heuristic_when_not_configured():
    cands = [_cand("a", 3, likes=1), _cand("b", 0, likes=999)]
    chosen = unsplash.choose_photo(cands, "Kyoto", rng=_FirstRng, config={"backend": "heuristic"})
    assert chosen["id"] == "b"


def test_choose_photo_uses_vision_scorer_when_configured():
    cands = [_cand("a", 0, likes=999), _cand("b", 1, likes=1)]
    fake_scorer = lambda c: 9.0 if c["id"] == "b" else 1.0
    cfg = {"backend": "openai", "url": "http://h/v1", "model": "m"}
    with patch("unsplash.make_vision_scorer", return_value=fake_scorer):
        chosen = unsplash.choose_photo(cands, "Kyoto", rng=_FirstRng, config=cfg)
    assert chosen["id"] == "b"


def test_choose_photo_none_when_all_below_min_res():
    cands = [{"id": "x", "_rank": 0, "likes": 5, "width": 1000, "height": 600}]
    assert unsplash.choose_photo(cands, "Kyoto", rng=_FirstRng, config={"backend": "heuristic"}) is None
