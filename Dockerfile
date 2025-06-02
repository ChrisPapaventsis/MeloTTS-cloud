# Use Python 3.9
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV APP_HOME /app
WORKDIR $APP_HOME

# Install MeCab and its development libraries, and a common dictionary
# Also install git, as some Python packages might need it for installation
RUN apt-get update && apt-get install -y \
    mecab \
    libmecab-dev \
    mecab-ipadic-utf8 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Cloud Run will set the PORT environment variable (default 8080)
ENV PORT 8080
EXPOSE 8080

# Command to run your function using functions-framework
CMD ["functions-framework", "--target=melo_tts_http", "--source=main.py"]