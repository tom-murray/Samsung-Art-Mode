from unittest.mock import patch

import openai_scorer


class _Resp:
    def __init__(self, status=200, json_data=None):
        self.status_code = status
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _chat(content, finish="stop", reasoning=None):
    msg = {"content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return _Resp(200, {"choices": [{"message": msg, "finish_reason": finish}]})


def test_parse_result_json_with_reason():
    assert openai_scorer._parse_result('{"score": 8, "reason": "clean skyline"}') == (8.0, "clean skyline")


def test_parse_result_bare_number_has_no_reason():
    assert openai_scorer._parse_result("Score: 7.5/10") == (7.5, None)


def test_parse_result_garbage_is_none_none():
    assert openai_scorer._parse_result("no number here") == (None, None)
    assert openai_scorer._parse_result("") == (None, None)


def test_parse_result_strips_box_tokens():
    # GLM-4.6V-Flash wraps its JSON in <|begin_of_box|>...<|end_of_box|>; the
    # reason must survive, not just the score.
    content = '\n<|begin_of_box|>{"score": 9, "reason": "Perfect Dubai skyline shot"}<|end_of_box|>'
    assert openai_scorer._parse_result(content) == (9.0, "Perfect Dubai skyline shot")


def test_parse_result_strips_markdown_fence():
    content = '```json\n{"score": 8, "reason": "nice"}\n```'
    assert openai_scorer._parse_result(content) == (8.0, "nice")


def test_score_image_builds_request_and_parses():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _chat('{"score": 9, "reason": "great"}')

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        result = openai_scorer.score_image(b"img", "Kyoto", url="http://h:1234/v1", model="m", api_key="k")
    assert result == (9.0, "great", "stop")
    assert captured["url"] == "http://h:1234/v1/chat/completions"
    assert captured["json"]["model"] == "m"
    assert captured["json"]["max_tokens"] == 1000  # default
    content = captured["json"]["messages"][0]["content"]
    assert "Kyoto" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_score_image_uses_configurable_max_tokens():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json=json)
        return _chat('{"score": 5, "reason": "x"}')

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        openai_scorer.score_image(b"img", "Kyoto", url="http://h/v1", model="m", max_tokens=1234)
    assert captured["json"]["max_tokens"] == 1234


def test_score_image_surfaces_finish_reason():
    def fake_post(url, json=None, headers=None, timeout=None):
        return _chat("", finish="length")  # truncated: empty content, no reasoning

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        result = openai_scorer.score_image(b"img", "Kyoto", url="http://h/v1", model="m")
    assert result == (None, None, "length")


def test_parse_result_trailing_brace_keeps_reason():
    # Valid JSON followed by an unrelated brace — non-greedy match keeps the reason
    # instead of over-grabbing to the last '}' and losing it.
    assert openai_scorer._parse_result('{"score": 8, "reason": "ok"} note {x}') == (8.0, "ok")


def test_score_image_ignores_stray_numbers_in_truncated_reasoning():
    # finish=length: content empty, reasoning is cut-off prose with incidental
    # numbers but no JSON. Must NOT invent a score from "3 people" — fall through
    # to (None, ...) so the caller applies the neutral 5.0.
    def fake_post(url, json=None, headers=None, timeout=None):
        return _chat("", finish="length",
                     reasoning="I count 3 people, on a 0 to 10 scale this is about")

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        result = openai_scorer.score_image(b"img", "Kyoto", url="http://h/v1", model="m")
    assert result == (None, None, "length")


def test_score_image_falls_back_to_reasoning_content():
    # Reasoning model finished (stop) but left content empty and put the answer
    # in reasoning_content — salvage the score from there.
    def fake_post(url, json=None, headers=None, timeout=None):
        return _chat("", finish="stop", reasoning='I rate this {"score": 6, "reason": "ok"}')

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        result = openai_scorer.score_image(b"img", "Kyoto", url="http://h/v1", model="m")
    assert result == (6.0, "ok", "stop")


def test_make_vision_scorer_returns_model_score():
    cfg = {"url": "http://h/v1", "model": "m", "timeout": 5}
    with patch("openai_scorer._fetch_small", return_value=b"img"), \
         patch("openai_scorer.score_image", return_value=(8.0, "nice", "stop")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p1"}) == 8.0


def test_make_vision_scorer_passes_max_tokens_from_config():
    cfg = {"url": "http://h/v1", "model": "m", "max_tokens": 321}
    captured = {}

    def fake_score(img, destination, **kwargs):
        captured.update(kwargs)
        return (7.0, "ok", "stop")

    with patch("openai_scorer._fetch_small", return_value=b"img"), \
         patch("openai_scorer.score_image", side_effect=fake_score):
        openai_scorer.make_vision_scorer("Kyoto", cfg)({"id": "p1"})
    assert captured["max_tokens"] == 321


def test_make_vision_scorer_neutral_on_failure():
    cfg = {"url": "http://h/v1", "model": "m"}
    with patch("openai_scorer._fetch_small", side_effect=RuntimeError("down")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p1"}) == 5.0


def test_make_vision_scorer_logs_score_reason_and_finish(caplog):
    cfg = {"url": "http://h/v1", "model": "m"}
    with caplog.at_level("INFO"), \
         patch("openai_scorer._fetch_small", return_value=b"img"), \
         patch("openai_scorer.score_image", return_value=(8.5, "clean skyline", "stop")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        scorer({"id": "p2"})
    assert any("p2" in r.message and "clean skyline" in r.message and "finish=stop" in r.message
               for r in caplog.records)


def test_make_vision_scorer_logs_finish_on_no_score(caplog):
    cfg = {"url": "http://h/v1", "model": "m"}
    with caplog.at_level("INFO"), \
         patch("openai_scorer._fetch_small", return_value=b"img"), \
         patch("openai_scorer.score_image", return_value=(None, None, "length")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p3"}) == 5.0
    assert any("p3" in r.message and "no score parsed" in r.message and "finish=length" in r.message
               for r in caplog.records)


def test_make_vision_scorer_logs_failure(caplog):
    cfg = {"url": "http://h/v1", "model": "m"}
    with caplog.at_level("WARNING"), \
         patch("openai_scorer._fetch_small", side_effect=RuntimeError("down")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p9"}) == 5.0
    assert any("p9" in r.message and "FAILED" in r.message for r in caplog.records)


def test_scorer_config_reads_env(monkeypatch):
    monkeypatch.setenv("SCORER_BACKEND", "openai")
    monkeypatch.setenv("SCORER_URL", "http://h/v1")
    monkeypatch.setenv("SCORER_MODEL", "m")
    cfg = openai_scorer.scorer_config()
    assert cfg["backend"] == "openai" and cfg["url"] == "http://h/v1" and cfg["model"] == "m"


def test_scorer_config_reads_max_tokens(monkeypatch):
    monkeypatch.setenv("SCORER_MAX_TOKENS", "777")
    assert openai_scorer.scorer_config()["max_tokens"] == 777


def test_scorer_config_default_max_tokens(monkeypatch):
    monkeypatch.delenv("SCORER_MAX_TOKENS", raising=False)
    assert openai_scorer.scorer_config()["max_tokens"] == 1000


def test_no_secrets_or_base64_logged_at_info(caplog):
    """Privacy contract: the api_key, the raw base64 image, and the raw model
    response must never appear in the logs at INFO level."""
    import base64

    cfg = {"url": "http://h/v1", "model": "m", "api_key": "SECRET-KEY"}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _chat('{"score": 7, "reason": "ok"}')

    with caplog.at_level("INFO"), \
         patch("openai_scorer._fetch_small", return_value=b"IMAGEBYTES"), \
         patch("openai_scorer.requests.post", side_effect=fake_post):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p1", "urls": {"small": "http://img"}}) == 7.0

    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET-KEY" not in blob
    assert "base64" not in blob.lower()
    assert base64.b64encode(b"IMAGEBYTES").decode() not in blob
