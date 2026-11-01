"""The public demo: its banner and guided tour, and starting a visitor's copy of the cases over."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException

from caduceus.api.dependencies import LiveDep, SignedIn
from caduceus.tour import tour

DEMO_RESET_COOLDOWN = timedelta(minutes=1)

router = APIRouter()


@router.get("/demo")
def demo_info(live: LiveDep) -> dict:
    """Tells the UI whether to show the demo banner. Says nothing in a real deployment."""
    if not live.demo:
        return {"demo": False}
    return {"demo": True, "reset_every_hours": live.reset_every.total_seconds() / 3600,
            "last_reset_at": live.last_reset.isoformat(),
            "next_reset_at": (live.last_reset + live.reset_every).isoformat(),
            "tour": tour(live.svc, live.presenter)}


@router.post("/demo/reset")
def demo_reset(who: SignedIn, live: LiveDep) -> dict:
    if not live.demo:
        raise HTTPException(404, "Not found.")
    if live.now() - live.last_reset < DEMO_RESET_COOLDOWN:
        raise HTTPException(429, "The demo was reset less than a minute ago. Try again shortly.")
    live.reset()
    return demo_info(live)
