FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including MeCab
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsndfile1 \
    mecab-python3 \
    libmecab-dev \
    mecab-ipadic-utf8 \
    swig \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy only files needed for dependency installation first
COPY requirements.txt .
COPY setup.py .  # If setup.py lists dependencies or is needed by "pip install -e ."
# You might need to copy other files/dirs if setup.py depends on them to resolve dependencies

# Install Python dependencies
# If setup.py installs from requirements.txt, you might only need "pip install -e ."
# If not, or to be explicit:
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install -e . # If you still need this for your project structure or specific setup.py actions

# Download Unidic dictionary (for MeCab, after fugashi is installed)
RUN python -m unidic download

# Copy the rest of your application code
COPY . .

# Initialize your application's downloads (e.g., TTS models)
# Ensure this path is correct based on your project structure after COPY . .
RUN python init_downloads.py

# Set the entrypoint for Cloud Run using functions-framework and your main.py
# Cloud Run sets the PORT environment variable (default 8080)
CMD ["functions-framework", "--target=melo_tts_http", "--source=main.py"]