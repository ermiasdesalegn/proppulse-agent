# PropPulse backend for a free Hugging Face Docker Space (no card needed).
# Spaces run the container as UID 1000 and send traffic to port 7860.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 PORT=7860 LOG_FORMAT=json TRUST_PROXY_HEADERS=true \
    HOME=/home/user PATH=/home/user/.local/bin:$PATH

RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app

COPY --chown=user requirements.txt ./
RUN pip install --user -r requirements.txt

COPY --chown=user app ./app
COPY --chown=user migrations ./migrations
COPY --chown=user scripts ./scripts
COPY --chown=user evals/summary.json ./evals/summary.json
COPY --chown=user tests/fixtures/seed_embeddings.json ./tests/fixtures/seed_embeddings.json

EXPOSE 7860
# python -m app starts uvicorn on HOST:PORT. On Linux this is the same as:
#   uvicorn app.main:app --host 0.0.0.0 --port 7860
CMD ["python", "-m", "app"]
