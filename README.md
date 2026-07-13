# Samsung Art Mode API

This project is a Flask-based API that allows you to set art mode images on a Samsung Frame TV by fetching images from Unsplash. The API allows you to specify keywords for the image search, and the image is resized to fit the TV's resolution before being uploaded.

I personally use this in my smart home and trigger through Home Assistant automations. My network is zoned, adding Samsung TVs to an IOT VLAN whilst Home Assistant sits within a trusted highly protected VLAN. Samsungs WebSocket security prevents connecting direct from Home Assistant, this lightweight API solves that problem as I deploy into the same VLAN as my Frame TVs.

Want to know about the Home Assistant Automation? I have a calendar within Home Assistant for up and coming holidays (vacations), when my home alarm is disabled the automation is triggered. The destination from the calendar is passed through to the API.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Building the Docker Image](#building-the-docker-image)
- [Running the Docker Container](#running-the-docker-container)
- [API Usage](#api-usage)
- [Waking a sleeping TV (Wake-on-LAN)](#post-tvstv_ipwake)
- [Environment Variables](#environment-variables)
- [Diagnosing an unsupported TV](#diagnosing-an-unsupported-tv)
- [Home Assistant integration](#home-assistant-integration)
- [Examples](#examples)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Features

- Fetches random images from Unsplash based on specified keywords.
- Resizes images to fit the Samsung Frame TV resolution (3840x2160).
- Uploads the image to the TV and sets it as the current art mode image.
- Deletes currently set image

## Prerequisites

- **Docker**: Make sure you have Docker installed on your system.
- **Unsplash API Key**: You need an Unsplash API access key to use the image search feature.

## Installation

1. Clone the repository:

```bash
git clone https://github.com/tom-murray/Samsung-Art-Mode.git
cd samsung-art-mode
```

2. Create a requirements.txt file (if not already present) with the following content:

```
Flask==2.3.3
Pillow==9.2.0
requests==2.31.0
samsungtvws==3.0.5
```

## Building the Docker Image

Build the Docker image using the following command:

```bash
docker build -t samsung-art-mode-api .
```

This command creates a Docker image named samsung-art-mode-api.

## Running the Docker Container

Run the Docker container:

```bash
docker run -d -p 5000:5000 --name samsung-art-mode samsung-art-mode-api
```

If port 5000 is already in use, you can map the container to a different port:

```bash
docker run -d -p 5001:5000 --name samsung-art-mode samsung-art-mode-api
```

## API Usage

The API exposes four endpoints: a health check, a per-TV diagnostic, a one-time
pairing step, and the art-mode update itself.

### GET /health

A simple liveness check for the service itself (no TV contacted).

```plaintext
GET http://<your_server_ip>:5000/health
```

```json
{
  "status": "ok"
}
```

### GET /tvs/<tv_ip>/diagnose

Runs a set of diagnostic checks against a TV and returns the results as JSON:
REST device info (from the TV's `/api/v2/` endpoint), whether a WebSocket
connection succeeds on port 8002 (SSL + token) and on port 8001 (legacy, no
SSL), whether art mode is supported, the art API version, and the currently
set art mode image. This is the first thing to reach for when a TV isn't
behaving — run it against the TV that isn't working and against one that is,
then compare the two outputs.

```plaintext
GET http://<your_server_ip>:5000/tvs/192.168.10.10/diagnose
```

```json
{
  "ip": "192.168.10.10",
  "rest": {
    "modelName": "UE43LS003",
    "name": "Frame TV",
    "FrameTVSupport": "true",
    "TokenAuthSupport": "true",
    "PowerState": "on",
    "error": null
  },
  "ports": {
    "8002": {
      "supported": true,
      "api_version": "4.3.4.0",
      "current": { "content_id": "MY-C0002" },
      "errors": {}
    },
    "8001": {
      "error": "ConnectionFailure(...)"
    }
  }
}
```

Each port entry is either a probe result (`supported`, `api_version`, `current`,
plus a per-call `errors` map) or, if the connection to that port failed
entirely, just an `error` string. Comparing the working and non-working TV's
`ports` block is what pinpoints the cause (e.g. old TV only responds on `8001`,
or `supported` is `false`).

### GET /tvs/<tv_ip>/state

Reports whether a TV is on, in standby, or unreachable, and — when it's awake —
whether it's actively displaying art. **Read-only: it never sends Wake-on-LAN,
so polling it won't disturb a sleeping TV.**

```plaintext
GET http://<your_server_ip>:5000/tvs/192.168.10.10/state
```

```json
{
  "reachable": true,
  "power": "on",
  "art_mode": "off",
  "summary": "on (watching)",
  "model": "QE65LS03RAUXXU",
  "name": "[TV] Kitchen TV",
  "error": null
}
```

- **`power`** comes from REST and is reliable even in standby: `on`,
  `standby`, or `unreachable`.
- **`art_mode`** (`on`/`off`) is a websocket query and is only available while
  the TV is awake; it reports `unknown` when the TV is asleep, because older
  Frame TVs shut their websocket in standby. For those, `power: standby`
  effectively means "showing art" (that's the Frame's off-state).
- **`summary`** is a friendly roll-up: `art mode`, `on (watching)`, `standby
  (...)`, or `off`.

Every response has the same keys. When the TV is fully powered off it isn't on
the network, so REST fails and you get `reachable: false`, `power: "off"`,
`summary: "off"`. `model`/`name` are filled from a small **last-known cache**
(persisted in `TOKEN_DIR`, so it survives rebuilds) — so even an off TV still
reports which TV it is, once it's been seen online at least once. A transient
network drop is indistinguishable from off — `reachable: false` is there so a
consumer can debounce it if desired.

### POST /tvs/<tv_ip>/wake

Wakes a sleeping TV with a Wake-on-LAN magic packet. Older Frame TVs (e.g. the
2019 `LS03R`) close their websocket control ports while in standby — only the
REST endpoint answers — so any control call would otherwise hang or be refused.
`pair` and `art-mode` call this automatically before connecting, so you rarely
need it directly; it's exposed mainly for testing.

The TV's MAC is auto-detected from its REST info (which answers even in
standby). You can override it by passing `mac` in the body.

```plaintext
POST http://<your_server_ip>:5000/tvs/192.168.10.10/wake
Content-Type: application/json

{ "mac": "AA:BB:CC:DD:EE:FF" }
```

```json
{ "woken": true, "mac": "AA:BB:CC:DD:EE:FF" }
```

`woken` is `false` (with a `reason`) if the TV was already awake, no MAC could
be found, or it didn't come up in time. Wake-on-LAN only works when the API and
the TV are on the same subnet (broadcast domain) — which they are in the
intended IoT-VLAN deployment.

### POST /tvs/<tv_ip>/pair

Opens a connection to the TV so it shows an "Allow" popup on screen. Accept it
using the TV's remote control **once** per TV; this saves a per-TV auth token
(see [Environment Variables](#environment-variables) below for where the
token is stored) so future requests don't need the popup again. Run this once
before the first `art-mode` call for any new TV.

```plaintext
POST http://<your_server_ip>:5000/tvs/192.168.10.10/pair
```

Returns HTTP `200` when a token was captured, or `504` if the popup wasn't
accepted before the timeout. The response body reports the outcome, which port
was used, where the token was stored, and any error:

```json
{
  "paired": true,
  "port": 8002,
  "token_file": "/data/tokens/192.168.10.10.txt",
  "error": null
}
```

### POST /tvs/<tv_ip>/art-mode

Fetches a random image from Unsplash matching the given keywords, resizes it
to fit the TV's resolution, uploads it, and sets it as the current art mode
image.

#### Request Body

```json
{
  "access_key": "Your Unsplash API access key",
  "keywords": "A comma-separated list of keywords for the image search, with no spaces!"
}
```

`access_key` is optional if the `UNSPLASH_ACCESS_KEY` environment variable is
set on the server; a value in the request body always takes priority. An
optional `mac` may be included to force a specific Wake-on-LAN target
(otherwise the MAC is auto-detected). A sleeping TV is woken automatically
before the upload.

#### Example Request

```plaintext
POST http://<your_server_ip>:5000/tvs/192.168.10.10/art-mode
Content-Type: application/json

{
  "access_key": "your_unsplash_access_key",
  "keywords": "Dubai,cityscape"
}
```

#### Response

The API will respond with a JSON object indicating success or failure.

```json
{
  "status": "success",
  "message": "Art updated from keywords: Dubai,cityscape",
  "uploaded_id": "MY-F0032",
  "port": 8002,
  "photo": {
    "id": "abc123",
    "description": "Dubai skyline at dusk",
    "photographer": "Jane Doe",
    "source_url": "https://unsplash.com/photos/abc123"
  }
}
```

The image is chosen by **relevance + quality** — an Unsplash *search* (landscape,
high-resolution, `content_filter=high`) rather than a random photo, skipping the
image shown last on that TV so it varies. The `photo` block reports which image
was used (with photographer attribution). Optionally an on-device vision model
re-ranks the shortlist for the best framed-art look (see
[Environment Variables](#environment-variables)).

Errors are returned with an appropriate status code and an `error` field:
`400` (missing `keywords`/`access_key` or empty keywords), `502` (Unsplash
request failed), or `500` (uploading to the TV failed).

## Environment Variables

- **`UNSPLASH_ACCESS_KEY`** (optional) — a default Unsplash API access key
  used when a request to `POST /tvs/<tv_ip>/art-mode` doesn't include its own
  `access_key`.
- **`TOKEN_DIR`** (default `/data/tokens`) — the directory where per-TV
  WebSocket auth tokens are saved after pairing. When running in Docker,
  mount a volume at this path so tokens survive container rebuilds, e.g.
  `-v samsung-art-mode-tokens:/data/tokens`.
- **`LOG_LEVEL`** (optional, default `INFO`) — log verbosity for the request
  trace. `DEBUG` adds raw LLM responses; base64 images are never logged.
- **`UNSPLASH_STRATEGY`** — how the candidate pool is sourced. `random` draws a
  fresh random set of matches each request (variety; relies on the vision scorer
  for quality). `relevant` uses the deterministic relevance search (most-iconic,
  but the same photos every time). If unset, it defaults to `random` **only when
  a vision scorer is configured** (so the scorer gates quality), otherwise
  `relevant` — set it explicitly to override.
- **`HISTORY_SIZE`** (default `20`) — how many recently-shown photos to remember
  per TV and exclude from the next pick, so the rotation doesn't repeat.

### Vision scorer (optional)

By default, images are ranked by quality heuristics (relevance, popularity,
aspect ratio, resolution). You can optionally have a vision model re-rank the
shortlist for the best "framed wall art" look. It works with **any
OpenAI-compatible endpoint** (LM Studio, Ollama `/v1`, vLLM, LocalAI, OpenAI):

- **`SCORER_BACKEND`** — `heuristic` (default) or `openai`.
- **`SCORER_URL`** — the endpoint base, e.g. `http://<your-model-host>:1234/v1`.
- **`SCORER_MODEL`** — the model name to request.
- **`SCORER_API_KEY`** (optional) — sent as a Bearer token if set.
- **`SCORER_TIMEOUT`** (default `30`) — per-image request timeout in seconds.
- **`SCORER_MAX_TOKENS`** (default `1000`) — response token budget. Reasoning
  models need enough headroom to finish thinking *and* emit the JSON answer; if
  the trace shows `finish=length`, raise this.

With `SCORER_BACKEND=openai` and `SCORER_URL`/`SCORER_MODEL` set, each candidate
is scored 0–10 by the model as framed wall art of the destination: it rewards
landmarks and points of interest, skylines and cityscapes, aerial shots,
heritage sites and attractions, and penalises off-topic, people-as-subject,
interiors, signage and dull composition. With a random pool the scorer is the
quality gate: the pool is ranked by score and the pick is a small random choice
among the top few, excluding photos recently shown on that TV. If the endpoint
is unset or unreachable it falls back to the heuristics — art updates never fail
because of the scorer.

**Tested with** the lightweight [`zai-org/glm-4.6v-flash`](https://lmstudio.ai/models/zai-org/glm-4.6v-flash)
vision model in LM Studio — a small, fast 9B model that scores each image in
~1.5–4s. Note it's a *reasoning* model: it spends tokens "thinking" before it
answers, so keep `SCORER_MAX_TOKENS` generous (the `1000` default is fine) or it
gets truncated (`finish=length` in the trace) and never emits a score. Any
OpenAI-compatible vision model works; non-reasoning models are cheaper on tokens.

### Request tracing

Every art-mode request emits a timestamped, correlation-id-tagged trace to the
container logs (search → shortlist scores → per-image LLM score + reason → pick →
upload → total time). Follow a single request with
`docker logs samsung-art-mode | grep <id>`, where `<id>` is the `[id]` shown on each
log line. Set `LOG_LEVEL=DEBUG` for extra detail (raw LLM responses); base64 image
data is never logged.

## Diagnosing an unsupported TV

If art mode isn't working on a particular TV, call
`GET /tvs/<tv_ip>/diagnose` (documented above) against it and, if you have
one, against a TV that *is* working, then compare the two JSON responses. A
ready-made Postman collection covering all four endpoints is included at
[`postman/samsung-art-mode.postman_collection.json`](postman/samsung-art-mode.postman_collection.json) —
import it into Postman and set the `base_url` and `tv_ip` collection
variables to get started quickly.

**A common gotcha — the TV is asleep.** If `diagnose` shows `PowerState:
standby` and the `8001`/`8002` port probes error while `rest.error` is `null`,
the TV is reachable but has shut its websocket control ports in standby (older
Frame TVs do this). The API wakes the TV automatically via Wake-on-LAN before
`pair`/`art-mode`, but the API and TV must be on the same subnet for the
broadcast to reach it. `PowerState: on` with the port probes returning
`supported: true` means the TV is fully working.

## Home Assistant integration

No custom integration is needed — Home Assistant's built-in `rest_command` (to
change art) and `rest` sensors (to read state) are enough. HA sits in the
trusted VLAN and calls across to this API on the IoT VLAN.

Set `UNSPLASH_ACCESS_KEY` in the container environment so HA never sends the key
on the wire — requests then carry only `keywords`.

### Actions (`configuration.yaml`)

```yaml
rest_command:
  samsung_art_set:
    url: "http://<your_server_ip>:5000/tvs/{{ tv_ip }}/art-mode"
    method: POST
    content_type: "application/json"
    payload: '{"keywords": "{{ keywords }}"}'
    timeout: 200          # art upload can take a while; must exceed the API's
                          # own caps. HA's rest_command default is only 10s.

  samsung_tv_wake:
    url: "http://<your_server_ip>:5000/tvs/{{ tv_ip }}/wake"
    method: POST
    content_type: "application/json"
    payload: "{}"
```

`/art-mode` already wakes a sleeping TV, so a separate wake step isn't needed.

### State sensors

`/state` is read-only and never wakes the TV, so it's safe to poll.

```yaml
rest:
  - resource: "http://<container ip>:5000/tvs/{{ tv_ip }}/state"   # Kitchen
    scan_interval: 60
    timeout: 15
    sensor:
      - name: "Kitchen TV art state"
        value_template: "{{ value_json.summary }}"
        json_attributes: [power, art_mode, reachable, model, name]
    binary_sensor:
      - name: "Kitchen TV online"
        value_template: "{{ value_json.reachable }}"
        device_class: connectivity
  - resource: "http://<container ip>:5000/tvs/{{ tv_ip }}/state"   # Living Room
    scan_interval: 60
    timeout: 15
    sensor:
      - name: "Living Room TV art state"
        value_template: "{{ value_json.summary }}"
        json_attributes: [power, art_mode, reachable, model, name]
```

### Example automation

Change both Frames to art of the next holiday destination when the alarm is
disarmed (swap `calendar.holidays` / `alarm_control_panel.home` for your own
entities):

```yaml
automation:
  - alias: "Frame art from next holiday when alarm disarmed"
    trigger:
      - platform: state
        entity_id: alarm_control_panel.home
        to: "disarmed"
    action:
      - variables:
          dest: "{{ state_attr('calendar.holidays', 'message') | default('landscape', true) }}"
      - service: rest_command.samsung_art_set
        data: { tv_ip: "<kitchen_tv_ip>", keywords: "{{ dest }}" }
      - service: rest_command.samsung_art_set
        data: { tv_ip: "<living_room_tv_ip>", keywords: "{{ dest }}" }
```

If you later add API authentication, add a matching `headers:` block to each
`rest_command` and `rest` resource — nothing else changes.

## Acknowledgements

This project leverages several open-source tools and libraries. Special thanks to the creators of:

- **[Flask](https://flask.palletsprojects.com/)**: A lightweight WSGI web application framework in Python, which makes it easy to create web applications.
- **[Pillow](https://python-pillow.org/)**: A powerful image processing library in Python that allows for resizing, cropping, and manipulating images.
- **[Requests](https://docs.python-requests.org/)**: A simple and elegant HTTP library for Python, making HTTP requests much easier to work with.
- **[samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api)**: A Python library that provides a WebSocket-based API to interact with Samsung Smart TVs, making it possible to control the TV and manage art mode.

## License

Feel free to take my code and amend as you desire.
