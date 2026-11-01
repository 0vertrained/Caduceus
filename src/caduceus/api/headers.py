"""The security headers on every response: the same as netlify.toml's for the demo's pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from caduceus.app_factory import Settings

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; font-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def security_headers(settings: Settings) -> dict[str, str]:
    if settings.auth_mode != "oidc" or not settings.oidc_issuer:
        return SECURITY_HEADERS
    # The page fetches the provider's discovery document and calls its token endpoint, so it may connect there and
    # nowhere else; signing in and out are navigations, which CSP does not govern.
    policy = f"{SECURITY_HEADERS['Content-Security-Policy']}; connect-src 'self' {settings.oidc_issuer.rstrip('/')}/"
    return SECURITY_HEADERS | {"Content-Security-Policy": policy}
