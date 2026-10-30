# One image serves the API and the built review UI. Two build stages, one small runtime stage.

FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS wheel
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CADUCEUS_DATA_DIR=/app/data CADUCEUS_DB=/var/lib/caduceus/caduceus.sqlite3 CADUCEUS_UI_DIST=/app/ui/dist
RUN useradd --system --uid 10001 --home-dir /app caduceus \
 && mkdir -p /var/lib/caduceus && chown caduceus /var/lib/caduceus
COPY --from=wheel /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
WORKDIR /app
COPY --chown=root:root data ./data
COPY --from=ui --chown=root:root /ui/dist ./ui/dist
# Application files are owned by root and read-only to the service user; only the state volume is writable.
USER caduceus
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "caduceus.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
