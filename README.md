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
git clone https://github.com/yourusername/samsung-art-mode.git
cd samsung-art-mode
```

2. Create a requirements.txt file (if not already present) with the following content:

```
Flask==2.3.3
Pillow==9.2.0
requests==2.31.0
samsungtvws==2.6.0
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

### Endpoint

POST /tvs/<tv_ip>/art-mode

### Request Body

{
access_key: "Your Unsplash API access key",
keywords: "A comma-separated list of keywords for the image search, with no spaces!"
}

### Example Request

```plaintext

POST http://<your_server_ip>:5000/tv/192.168.10.10/art-mode
Content-Type: application/json

{
"access_key": "your_unsplash_access_key",
"keywords": "Dubai,cityscape"
}

```

### Response

The API will respond with a JSON object indicating success or failure.

```json
{
  "status": "success",
  "message": "Art mode image updated with a new image based on keywords: New York, skyline, sunset."
}
```

## Acknowledgements

This project leverages several open-source tools and libraries. Special thanks to the creators of:

- **[Flask](https://flask.palletsprojects.com/)**: A lightweight WSGI web application framework in Python, which makes it easy to create web applications.
- **[Pillow](https://python-pillow.org/)**: A powerful image processing library in Python that allows for resizing, cropping, and manipulating images.
- **[Requests](https://docs.python-requests.org/)**: A simple and elegant HTTP library for Python, making HTTP requests much easier to work with.
- **[samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api)**: A Python library that provides a WebSocket-based API to interact with Samsung Smart TVs, making it possible to control the TV and manage art mode.

## License

Feel free to take my code and amend as you desire.
