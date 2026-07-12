import logging
import os

from flask import Flask, jsonify, request

import tvcontrol
import unsplash

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


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
    data = request.get_json(silent=True) or {}
    keywords = data.get("keywords")
    access_key = data.get("access_key") or os.environ.get("UNSPLASH_ACCESS_KEY")

    if not keywords:
        return jsonify(error="Missing required parameter: keywords"), 400
    if not access_key:
        return jsonify(error="Missing Unsplash access_key (body or UNSPLASH_ACCESS_KEY env)"), 400

    exclude_id = tvcontrol.last_photo(tv_ip)
    try:
        image, photo = unsplash.fetch_art_image(access_key, keywords, exclude_id=exclude_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except unsplash.UnsplashError as e:
        return jsonify(error=str(e)), 502

    try:
        result = tvcontrol.apply_art(tv_ip, image, mac=data.get("mac"))
    except Exception as e:  # noqa: BLE001
        logging.exception("art-mode failed for %s", tv_ip)
        return jsonify(error=str(e)), 500

    tvcontrol.record_photo(tv_ip, photo.get("id"))
    return jsonify(status="success", message=f"Art updated from keywords: {keywords}",
                   photo=photo, **result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
