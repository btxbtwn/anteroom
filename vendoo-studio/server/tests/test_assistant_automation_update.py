"""An assistant update changes only what the seller asked for: the rest of the task stays, and a paused task stays paused."""
import json

from vendoo_studio.services import automations

from test_automations import NOW, client, db  # noqa: F401


def _task(client):  # noqa: F811
    created = client.post("/api/automations", json={
        "name": "Old tees", "timezone": "UTC",
        "schedule": {"kind": "daily", "time": "09:00"},
        "filters": {"category": "T-Shirts", "min_age_days": 60},
        "actions": [{"type": "price", "mode": "percent", "value": 10, "floor": 15}],
        "marketplaces": ["ebay"], "activate": True,
    })
    assert created.status_code == 200, created.text
    return created.json()


def test_assistant_update_of_only_the_actions_keeps_filters_and_schedule(db, client):  # noqa: F811
    task = _task(client)
    block = json.dumps({"op": "update", "id": task["id"],
                        "actions": [{"type": "price", "mode": "percent", "value": 20, "floor": 15}]})
    proposal = automations.proposal_from_block(db, block, now=NOW)
    # The card shows the whole task after the change, so it is confirmable.
    assert proposal["issues"] == []
    assert proposal["schedule"] == task["schedule"]
    automations.confirm_proposal(db, proposal, now=NOW)
    saved = next(row for row in client.get("/api/automations").json() if row["id"] == task["id"])
    # Only the drop was asked to change; the category/age filter must still limit which items it touches.
    assert saved["filters"] == task["filters"]
    assert saved["schedule"] == task["schedule"]
    assert saved["marketplaces"] == task["marketplaces"]


def test_assistant_update_does_not_resume_a_paused_task(db, client):  # noqa: F811
    task = _task(client)
    client.post(f"/api/automations/{task['id']}/pause")
    block = json.dumps({"op": "update", "id": task["id"], "name": "Old tees renamed", "schedule": task["schedule"],
                        "filters": task["filters"], "actions": task["actions"], "marketplaces": task["marketplaces"]})
    proposal = automations.proposal_from_block(db, block, now=NOW)
    automations.confirm_proposal(db, proposal, now=NOW)
    saved = next(row for row in client.get("/api/automations").json() if row["id"] == task["id"])
    assert saved["status"] == "paused"


def test_assistant_moving_the_time_keeps_which_items_it_touches(db, client):  # noqa: F811
    task = _task(client)
    block = json.dumps({"op": "update", "id": task["id"], "schedule": {"kind": "daily", "time": "10:00"},
                        "actions": task["actions"]})
    proposal = automations.proposal_from_block(db, block, now=NOW)
    automations.confirm_proposal(db, proposal, now=NOW)
    saved = next(row for row in client.get("/api/automations").json() if row["id"] == task["id"])
    # Moving the time must not widen a T-Shirts/60-day price drop to every listing.
    assert saved["filters"] == task["filters"]
