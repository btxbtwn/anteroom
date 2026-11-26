"""Verify an isolated seller can save and reopen one internal draft after rollout."""
import json
import os
import re
import tomllib
from pathlib import Path
from uuid import uuid4

import httpx


INVENTORY = "https://myanteroom.com/api/conversations"
TITLE = "Anteroom production release check"


def verify(client, account, publishable_key):
    config = tomllib.loads(Path(__file__).with_name("fly.toml").read_text())
    auth = config["build"]["args"]["VITE_SUPABASE_URL"]
    response = client.post(
        f"{auth}/auth/v1/token?grant_type=password",
        headers={"apikey": publishable_key},
        json={"email": account["email"], "password": account["password"]},
    )
    response.raise_for_status()
    session = response.json()
    if not account["user_id"] or session["user"]["id"] != account["user_id"]:
        raise RuntimeError("Unexpected smoke seller identity")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    def request(method, url, **kwargs):
        response = client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    accounts = request("GET", "https://myanteroom.com/api/marketplace-accounts")["accounts"]
    if not {"ebay", "etsy"} <= accounts.keys() or any(
        account["connected"] is not False or account["pending"] is not False for account in accounts.values()
    ):
        raise RuntimeError("Release seller has a direct marketplace connection")
    sessions = request("GET", "https://myanteroom.com/api/marketplaces/sessions")
    if sessions.get("chrome_connected") is not False or any(
        item.get("cloud_connected") or item["state"] == "signed_in" for item in sessions["sessions"].values()
    ):
        raise RuntimeError("Release seller has a browser marketplace connection")

    rows = request("GET", INVENTORY)
    if not isinstance(rows, list) or len(rows) > 1:
        raise RuntimeError("Unexpected smoke seller inventory")
    if rows:
        row = rows[0]
    else:
        row = request("POST", INVENTORY, json={"title": TITLE})
    if (row["title"] != TITLE or row["status"] != "draft" or row.get("scheduled_publish_at")
            or row.get("direct_records") or not re.fullmatch(r"[0-9a-f]{12}", row["id"])):
        raise RuntimeError("Unexpected smoke seller inventory")
    url = f"{INVENTORY}/{row['id']}"
    marker = f"Release verification {uuid4()}"
    request("PATCH", url, json={"notes": marker})
    saved = request("GET", url)
    if saved["id"] != row["id"] or saved.get("notes") != marker or saved["status"] != "draft":
        raise RuntimeError("Internal draft readback failed")


if __name__ == "__main__":
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        verify(client, json.loads(os.environ["HOSTED_SMOKE_ACCOUNT"]), os.environ["SUPABASE_PUBLISHABLE_KEY"])
    print("The release seller saved and reopened its internal draft.")
