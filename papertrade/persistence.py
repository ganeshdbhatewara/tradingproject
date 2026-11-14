"""Atomic local state with an optional Supabase REST-backed durable cloud store."""
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, timezone
import requests
from .engine import Portfolio, load_portfolio, save_portfolio


def _cloud_settings() -> tuple[str, str, str] | None:
    url, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url and not key:
        return None
    if not url or not key:
        raise RuntimeError("Set both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, or neither")
    return url, key, os.getenv("PAPER_STATE_ID", "default")


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Accept": "application/json"}


def load_state(local_path: str | Path) -> tuple[Portfolio, str]:
    """Use configured cloud state exclusively; report backend so the UI can label it."""
    settings = _cloud_settings()
    if settings is None:
        return load_portfolio(local_path), "local JSON (not durable on ephemeral hosts)"
    url, key, state_id = settings
    response = requests.get(f"{url}/rest/v1/paper_state",
        params={"state_key": f"eq.{state_id}", "select": "payload,revision", "limit": "1"},
        headers=_headers(key), timeout=15)
    response.raise_for_status()
    rows = response.json()
    portfolio = Portfolio.from_dict(rows[0]["payload"]) if rows else Portfolio()
    portfolio._cloud_revision = int(rows[0]["revision"]) if rows else None
    return portfolio, "Supabase durable cloud state"


def save_state(portfolio: Portfolio, local_path: str | Path) -> str:
    settings = _cloud_settings()
    if settings is None:
        save_portfolio(portfolio, local_path)
        return "local JSON (not durable on ephemeral hosts)"
    url, key, state_id = settings
    revision = getattr(portfolio, "_cloud_revision", None)
    headers = {**_headers(key), "Prefer": "return=representation"}
    if revision is None:
        response = requests.post(f"{url}/rest/v1/paper_state",
            params={"on_conflict": "state_key"},
            headers={**headers, "Prefer": "resolution=ignore-duplicates,return=representation"},
            json={"state_key": state_id, "payload": portfolio.to_dict(), "revision": 1}, timeout=15)
        response.raise_for_status()
        rows = response.json()
        if not rows:
            raise RuntimeError("Cloud state was created concurrently; reload before saving")
        portfolio._cloud_revision = int(rows[0]["revision"])
        return "Supabase durable cloud state"
    response = requests.patch(f"{url}/rest/v1/paper_state",
        params={"state_key": f"eq.{state_id}", "revision": f"eq.{revision}"},
        headers=headers,
        json={"payload": portfolio.to_dict(), "revision": revision + 1,
              "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=15)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise RuntimeError("Cloud state changed since it was loaded; reload before saving")
    portfolio._cloud_revision = int(rows[0]["revision"])
    return "Supabase durable cloud state"
