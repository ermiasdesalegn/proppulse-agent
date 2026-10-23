# Backend image. Build:  docker build -t proppulse-backend backend
# JavaScript-page scraping needs Chromium; add it with:  --build-arg WITH_BROWSER=true  (adds about 500 MB)
FROM python:3.12-slim

ARG WITH_BROWSER=false
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=8080 LOG_FORMAT=json TRUST_PROXY_HEADERS=true

WORKDIR /srv
COPY requirements.txt requirements-browser.txt ./
RUN pip install -r requirements.txt \
 && if [ "$WITH_BROWSER" = "true" ]; then pip install -r requirements-browser.txt && playwright install --with-deps chromium; fi

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY evals/summary.json ./evals/summary.json
COPY tests/fixtures/seed_embeddings.json ./tests/fixtures/seed_embeddings.json

RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/health', timeout=4)"
CMD ["python", "-m", "app"]
