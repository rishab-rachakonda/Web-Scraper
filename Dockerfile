# Greatest Web Scraper — reproducible container with Chromium baked in.
FROM python:3.12-slim

WORKDIR /app

# Install Python deps first (better layer caching), then the Playwright browser
# plus its system libraries via --with-deps.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Scraped output and logs land here; mount a volume to persist them:
#   docker run -v "$PWD/output:/app/output" greatest-scraper run example_jobs/aggressive.yaml
ENTRYPOINT ["python", "scraper.py"]
CMD ["info"]
