FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

COPY requirements.txt . # Copies requirements.txt first for caching
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt # Installs from requirements.txt

COPY . . # Copies the rest of the application code

RUN if [ -f init_downloads.py ]; then python init_downloads.py; fi

EXPOSE 8080
# Uses Functions Framework to run your specific event-driven function from main.py
CMD ["functions-framework", "--target=melo_tts_gcs_trigger", "--source=main.py"]