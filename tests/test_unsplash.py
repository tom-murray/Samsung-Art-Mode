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


class _LastRng:
    """Deterministic stand-in: always the last item of the pool."""
    @staticmethod
    def choice(seq):
        return seq[-1]


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


def test_choose_photo_drops_candidates_below_top_k():
    # 6 eligible candidates scored a>b>c>d>e>f; only the top TOP_K (5) may be
    # selected, so the worst ("f") must never be picked even with a pool-last rng.
    cands = [_cand(x, 0) for x in "abcdef"]
    scores = {"a": 6, "b": 5, "c": 4, "d": 3, "e": 2, "f": 1}
    fake = lambda c: scores[c["id"]]
    cfg = {"backend": "openai", "url": "http://h/v1", "model": "m"}
    with patch("unsplash.make_vision_scorer", return_value=fake):
        chosen = unsplash.choose_photo(cands, "Kyoto", rng=_LastRng, config=cfg)
    assert chosen["id"] == "e"  # last of the top-5; "f" is excluded by scoring


def _photo(id="p1", width=5000, height=2813):
    return {"id": id, "width": width, "height": height, "likes": 100, "_rank": 0,
            "alt_description": "scene", "user": {"name": "Jane"},
            "urls": {"full": "https://img.example/full"},
            "links": {"html": "https://unsplash.com/photos/p1",
                      "download_location": "https://api.unsplash.com/photos/p1/download"}}


def test_fetch_art_image_uses_search_and_returns_meta():
    search = _Resp(200, {"results": [_photo()]})
    dl = _Resp(200, {})
    photo = _Resp(200, content=_png_bytes())
    with patch("unsplash.requests.get", side_effect=[search, dl, photo]):
        data, meta = unsplash.fetch_art_image("key", "Kyoto", size=(320, 180))
    assert Image.open(BytesIO(data)).size == (320, 180)
    assert meta["id"] == "p1" and meta["photographer"] == "Jane"


def test_fetch_art_image_excludes_last_shown():
    two = _Resp(200, {"results": [_photo("p1"), _photo("p2")]})
    dl = _Resp(200, {})
    photo = _Resp(200, content=_png_bytes())
    with patch("unsplash.requests.get", side_effect=[two, dl, photo]):
        _, meta = unsplash.fetch_art_image("key", "Kyoto", size=(320, 180), exclude_id="p1", rng=_FirstRng)
    assert meta["id"] == "p2"


def test_fetch_art_image_falls_back_to_random_on_empty_search():
    empty = _Resp(200, {"results": []})
    rand = _Resp(200, _photo())
    dl = _Resp(200, {})          # download trigger fires on the random path too
    photo = _Resp(200, content=_png_bytes())
    with patch("unsplash.requests.get", side_effect=[empty, rand, dl, photo]):
        data, meta = unsplash.fetch_art_image("key", "Kyoto", size=(320, 180))
    assert meta["id"] == "p1"


def test_fetch_art_image_raises_on_empty_keywords():
    try:
        unsplash.fetch_art_image("key", " , ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_fetch_art_image_raises_when_chosen_has_no_full_url():
    # candidate passes the resolution gate but has no urls.full → clean UnsplashError
    bad = {"id": "p1", "width": 5000, "height": 2813, "_rank": 0, "urls": {}, "links": {}}
    with patch("unsplash.requests.get", side_effect=[_Resp(200, {"results": [bad]})]):
        try:
            unsplash.fetch_art_image("key", "Kyoto")
            assert False, "expected UnsplashError"
        except unsplash.UnsplashError:
            pass


def test_fetch_art_image_raises_when_search_and_random_both_fail():
    bad_search = _Resp(500, {})
    bad_random = _Resp(401, {})
    with patch("unsplash.requests.get", side_effect=[bad_search, bad_random]):
        try:
            unsplash.fetch_art_image("key", "Kyoto")
            assert False, "expected UnsplashError"
        except unsplash.UnsplashError:
            pass


def test_rank_candidates_sorts_by_score_desc_and_filters_min_res():
    good = _cand("b", 0, likes=999)
    poor = _cand("a", 3, likes=1)
    tiny = {"id": "x", "_rank": 0, "likes": 5, "width": 1000, "height": 600}
    ranked = unsplash.rank_candidates([poor, good, tiny], unsplash.heuristic_scorer)
    assert [c["id"] for c, _ in ranked] == ["b", "a"]  # tiny dropped, b outranks a
    assert ranked[0][1] > ranked[1][1]


def test_pick_from_ranked_respects_top_k_and_exclude():
    ranked = [(_cand(x, 0), score) for x, score in
              [("a", 6), ("b", 5), ("c", 4), ("d", 3), ("e", 2), ("f", 1)]]
    # last of top-5 with a pool-last rng is "e"; "f" is outside top_k
    chosen = unsplash.pick_from_ranked(ranked, top_k=5, rng=_LastRng)
    assert chosen["id"] == "e"


def test_pick_from_ranked_none_when_empty():
    assert unsplash.pick_from_ranked([], rng=_FirstRng) is None


def test_choose_photo_logs_funnel(caplog):
    cands = [_cand("a", 0, likes=999), _cand("b", 1, likes=1)]
    with caplog.at_level("INFO"):
        unsplash.choose_photo(cands, "Kyoto", rng=_FirstRng, config={"backend": "heuristic"})
    text = " ".join(r.message for r in caplog.records)
    assert "shortlist" in text and "picked" in text
