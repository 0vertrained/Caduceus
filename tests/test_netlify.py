"""netlify.toml: the public demo's front door. Keep it signed, and keep its headers in step with the API's."""

from __future__ import annotations

import tomllib
from pathlib import Path

from caduceus.api import SECURITY_HEADERS

CONFIG = tomllib.loads((Path(__file__).resolve().parents[1] / "netlify.toml").read_text())


def test_api_requests_are_proxied_signed_and_matched_first():
    api, spa = CONFIG["redirects"]
    assert api["from"] == "/api/*" and api["status"] == 200 and api["force"] is True
    assert api["signed"] == "CADUCEUS_PROXY_SIGNING_KEY"
    assert api["to"] == "https://CADUCEUS-API-ORIGIN.invalid/api/:splat"  # replaced by scripts/netlify_build.sh
    assert spa == {"from": "/*", "to": "/index.html", "status": 200}


def test_the_site_sends_the_same_security_headers_as_the_api():
    site = next(h for h in CONFIG["headers"] if h["for"] == "/*")["values"]
    assert site == SECURITY_HEADERS
