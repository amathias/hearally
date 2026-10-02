# Parakeet transcription job for Nebius Serverless AI (see README.md).
FROM python:3.12-slim

# ffmpeg converts and cuts audio; libsndfile reads it; build tools for the few
# NeMo dependencies that compile.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The model is baked into the image, so a job never waits on a download and
# always runs the exact weights that were tested.
ENV HF_HOME=/models
RUN python -c "import nemo.collections.asr as a; a.models.ASRModel.from_pretrained(model_name='nvidia/parakeet-tdt-0.6b-v3')"

COPY src ./src
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 transcriber && chmod -R a+rX /models /app
USER transcriber

ENTRYPOINT ["python", "-m", "hearally_transcriber.main"]
