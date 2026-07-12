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


def _chat(content):
    return _Resp(200, {"choices": [{"message": {"content": content}}]})


def test_parse_score_json_bare_and_none():
    assert openai_scorer._parse_score('{"score": 8}') == 8.0
    assert openai_scorer._parse_score("Score: 7.5/10") == 7.5
    assert openai_scorer._parse_score("no number here") is None


def test_score_image_builds_request_and_parses():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _chat('{"score": 9}')

    with patch("openai_scorer.requests.post", side_effect=fake_post):
        s = openai_scorer.score_image(b"img", "Kyoto", url="http://h:1234/v1", model="m", api_key="k")
    assert s == 9.0
    assert captured["url"] == "http://h:1234/v1/chat/completions"
    assert captured["json"]["model"] == "m"
    content = captured["json"]["messages"][0]["content"]
    assert "Kyoto" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_make_vision_scorer_returns_model_score():
    cfg = {"url": "http://h/v1", "model": "m", "timeout": 5}
    with patch("openai_scorer._fetch_small", return_value=b"img"), \
         patch("openai_scorer.score_image", return_value=8.0):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p1"}) == 8.0


def test_make_vision_scorer_neutral_on_failure():
    cfg = {"url": "http://h/v1", "model": "m"}
    with patch("openai_scorer._fetch_small", side_effect=RuntimeError("down")):
        scorer = openai_scorer.make_vision_scorer("Kyoto", cfg)
        assert scorer({"id": "p1"}) == 5.0


def test_scorer_config_reads_env(monkeypatch):
    monkeypatch.setenv("SCORER_BACKEND", "openai")
    monkeypatch.setenv("SCORER_URL", "http://h/v1")
    monkeypatch.setenv("SCORER_MODEL", "m")
    cfg = openai_scorer.scorer_config()
    assert cfg["backend"] == "openai" and cfg["url"] == "http://h/v1" and cfg["model"] == "m"
