# One image serves the API and the built review UI, on Chainguard's Wolfi base with Wolfi's own Python 3.13: a
# minimal distribution whose packages are patched within days. Base images are pinned by digest (Dependabot proposes
# new ones), and CI scans the result on every build.

# Build only: nothing from this stage ships except the static UI.
FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# Build only: uv, from its publisher's image.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:d13acb327aa77acf2752acea8c08456511a5b8142455cc2fe0f966b960bf64c6 AS python
RUN apk add --no-cache python-3.13
COPY --from=uv /uv /usr/local/bin/uv
# Only the files uv.lock lists, each checked against its hash; a lock that no longer matches pyproject.toml stops the
# build. The venv keeps no installer: no pip, and setuptools goes once it has built Caduceus. Bytecode is compiled
# here because the runtime image is read-only.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON=/usr/bin/python3.13 UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-editable --no-install-project
COPY src ./src
RUN uv sync --locked --no-editable \
 && uv pip uninstall --python /opt/venv/bin/python setuptools

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:d13acb327aa77acf2752acea8c08456511a5b8142455cc2fe0f966b960bf64c6
# Tesseract reads faxed referrals inside the container: identifiable pages never go to an outside service.
RUN apk add --no-cache python-3.13 tesseract tesseract-eng \
 && adduser -D -H -h /app -u 10001 caduceus \
 && mkdir -p /var/lib/caduceus && chown caduceus /var/lib/caduceus
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CADUCEUS_DATA_DIR=/app/data CADUCEUS_DB=/var/lib/caduceus/caduceus.sqlite3 CADUCEUS_UI_DIST=/app/ui/dist
COPY --from=python /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root data ./data
COPY --from=ui --chown=root:root /ui/dist ./ui/dist
# Application files are owned by root and read-only to the service user; only the state volume is writable.
USER caduceus
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "caduceus.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
