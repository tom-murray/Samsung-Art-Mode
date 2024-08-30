from flask import Flask, request, jsonify
import logging
import requests
from samsungtvws import SamsungTVWS
from PIL import Image, ImageOps
from io import BytesIO
import os

app = Flask(__name__)

# Increase debug level
logging.basicConfig(level=logging.DEBUG)

@app.route('/tvs/<tv_ip>/art-mode', methods=['POST'])
def set_art_mode(tv_ip):
    data = request.get_json()

    if not data or 'access_key' not in data or 'keywords' not in data:
        return jsonify({"error": "Missing required parameters: access_key and keywords"}), 400

    access_key = data['access_key']
    keywords = data['keywords']

    try:
        # Convert the comma-separated list of keywords into a query string
        query = ','.join([kw.strip() for kw in keywords.split(',')])

        # Set up the Unsplash API headers
        headers = {'Authorization': f'Client-ID {access_key}'}

        # Call Unsplash API to get a random image based on the keywords
        image_query = requests.get(f"https://api.unsplash.com/photos/random?orientation=landscape&query={query}", headers=headers)
        image_data = image_query.json()
        image_url = image_data['urls']['full'] + "&w=3840&h=2160"

        # Download the image
        response = requests.get(image_url)
        img = Image.open(BytesIO(response.content))

        # Resize the image while maintaining aspect ratio
        target_size = (3840, 2160)  # Target size for the TV
        img = ImageOps.fit(img, target_size, Image.LANCZOS)

        # Save the image
        img_path = 'bg.jpg'
        img.save(img_path, optimize=True, quality=100)

        # Initialize SamsungTVWS
        token_file = os.path.dirname(os.path.realpath(__file__)) + '/tv-token.txt'
        tv = SamsungTVWS(host=tv_ip, port=8002, token_file=token_file)

        # Get current image
        current_img = tv.art().get_current()

        # Upload new image
        with open(img_path, 'rb') as file:
            img_data = file.read()
            uploadedID = tv.art().upload(img_data, file_type="JPEG", matte='none')

        # Select the new image as the active one
        tv.art().select_image(uploadedID)

        # Delete old image
        tv.art().delete(current_img['content_id'])

        return jsonify({"status": "success", "message": f"Art mode image updated with a new image based on keywords: {keywords}."}), 200

    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)