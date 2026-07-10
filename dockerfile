# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the current directory contents into the container at /usr/src/app
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Define environment variable
ENV FLASK_APP=app.py

# Per-TV auth tokens live here; mount a volume so they survive rebuilds
ENV TOKEN_DIR=/data/tokens

# Run under gunicorn with the threaded worker so one slow TV call doesn't block
# other requests (the default sync worker ignores --threads). --timeout 180 is a
# backstop that recycles a worker if a call ever hangs past our own per-call caps.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--workers", "2", "--threads", "8", "--timeout", "180", "app:app"]
