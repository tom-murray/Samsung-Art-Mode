import logging
import os
import time

from flask import Flask, jsonify, request

import openai_scorer
import tracing
import tvcontrol
import unsplash

app = Flask(__name__)
tracing.configure_logging()
log = logging.getLogger("app")
log.addFilter(tracing.RequestIdFilter())


@app.before_request
def _tag_request():
    tracing.new_request_id()


@app.get("/health")
def health():
    return jsonify(status="ok"), 200


@app.get("/tvs/<tv_ip>/diagnose")
def diagnose(tv_ip):
    return jsonify(tvcontrol.diagnose(tv_ip)), 200


@app.get("/tvs/<tv_ip>/state")
def state(tv_ip):
    return jsonify(tvcontrol.state(tv_ip)), 200


@app.post("/tvs/<tv_ip>/wake")
def wake(tv_ip):
    data = request.get_json(silent=True) or {}
    result = tvcontrol.ensure_awake(tv_ip, mac=data.get("mac"))
    return jsonify(result), 200


@app.post("/tvs/<tv_ip>/pair")
def pair(tv_ip):
    data = request.get_json(silent=True) or {}
    result = tvcontrol.pair(tv_ip, mac=data.get("mac"))
    return jsonify(result), (200 if result["paired"] else 504)


@app.post("/tvs/<tv_ip>/art-mode")
def art_mode(tv_ip):
    start = time.monotonic()
    data = request.get_json(silent=True) or {}
    keywords = data.get("keywords")
    access_key = data.get("access_key") or os.environ.get("UNSPLASH_ACCESS_KEY")

    if not keywords:
        return jsonify(error="Missing required parameter: keywords"), 400
    if not access_key:
        return jsonify(error="Missing Unsplash access_key (body or UNSPLASH_ACCESS_KEY env)"), 400

    exclude_id = tvcontrol.last_photo(tv_ip)
    backend = openai_scorer.scorer_config().get("backend")
    log.info("art-mode tv=%s keywords=%r backend=%s exclude_last=%s",
             tv_ip, keywords, backend, exclude_id)
    try:
        image, photo = unsplash.fetch_art_image(access_key, keywords, exclude_id=exclude_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except unsplash.UnsplashError as e:
        log.warning("art-mode aborted: %s", e)
        return jsonify(error=str(e)), 502

    try:
        result = tvcontrol.apply_art(tv_ip, image, mac=data.get("mac"))
    except Exception as e:  # noqa: BLE001
        log.exception("art-mode failed for %s", tv_ip)
        return jsonify(error=str(e)), 500

    tvcontrol.record_photo(tv_ip, photo.get("id"))
    log.info("done success in %.1fs", time.monotonic() - start)
    return jsonify(status="success", message=f"Art updated from keywords: {keywords}",
                   photo=photo, **result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
