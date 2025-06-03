FROM python:3.11-slim
WORKDIR /app
COPY . /app

RUN apt-get update && apt-get install -y \
    build-essential libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

RUN pip install -e .
RUN python init_downloads.py

CMD ["functions-framework", "--target=melo_tts_gcs_trigger", "--port=8080"]