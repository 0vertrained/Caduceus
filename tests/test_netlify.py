"""netlify.toml and the edge function beside it: the public demo's front door. Keep it signed, keep its headers
in step with the API's, and keep the edge looking for the header the API reads."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from caduceus.api import SECURITY_HEADERS
from caduceus.api.middleware import NOT_COUNTED
from caduceus.api.sandboxes import SANDBOX_HEADER

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "netlify.toml").read_text())
EDGE = (ROOT / "ui/netlify/edge-functions/pages-only.js").read_text()


def test_api_requests_are_proxied_signed_and_matched_first():
    api, spa = CONFIG["redirects"]
    assert api["from"] == "/api/*" and api["status"] == 200 and api["force"] is True
    assert api["signed"] == "CADUCEUS_PROXY_SIGNING_KEY"
    assert api["to"] == "https://CADUCEUS-API-ORIGIN.invalid/api/:splat"  # replaced by scripts/netlify_build.sh
    assert spa == {"from": "/*", "to": "/index.html", "status": 200}


def test_the_site_sends_the_same_security_headers_as_the_api():
    site = next(h for h in CONFIG["headers"] if h["for"] == "/*")["values"]
    assert site == SECURITY_HEADERS


def test_the_edge_knows_a_pages_request_by_the_header_the_api_reads():
    """ui/netlify/edge-functions/pages-only.js answers API requests without the sandbox id itself. The one address
    it lets through without it is what a page asks first, which the API answers without a copy of the cases."""
    assert re.search(r'const SANDBOX = "([^"]+)";', EDGE).group(1) == SANDBOX_HEADER
    assert re.search(r'const FIRST_REQUEST = "([^"]+)";', EDGE).group(1) in NOT_COUNTED
