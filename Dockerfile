# One image serves the API and the built review UI.
# The runtime is Chainguard's Wolfi base with Python 3.13 from Wolfi's own packages: a minimal glibc
# distribution whose packages are patched for known vulnerabilities within days. The Debian-based official
# Python image carried 236 vulnerabilities with no fix released; CI scans the image on every build.

# Base images are pinned by digest (Dependabot proposes new ones). Wolfi packages installed with apk are
# the current patched builds at build time, and CI scans the result.

# Build only: nothing from this stage ships except the static UI it produces.
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS python
RUN apk add --no-cache python-3.13 py3.13-pip
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
# A venv without pip: the runtime image needs the packages, not the installer.
RUN python3.13 -m venv --without-pip /opt/venv \
 && python3.13 -m pip --python /opt/venv/bin/python install --no-cache-dir .

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73
RUN apk add --no-cache python-3.13 \
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
