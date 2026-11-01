"""Automations on the web, using the desktop handlers and services.

Saving, previewing, pausing and running tasks only touch the seller's own rows
or queue a draft regenerate. Approving a marketplace action needs web
marketplace actions, which are not connected yet, so it is refused here.
"""
from fastapi import APIRouter, HTTPException

from vendoo_studio.routes import automations, errors

router = APIRouter(prefix='/api/automations', tags=['automations'])
for path, handler, methods in (
    ('', automations.list_automations, ['GET']),
    ('/draft', automations.draft_automation, ['POST']),
    ('/preview', automations.preview_automation, ['POST']),
    ('/approvals', automations.list_approvals, ['GET']),
    ('', automations.create_automation, ['POST']),
    ('/{automation_id}', automations.update_automation, ['PATCH']),
    ('/{automation_id}/pause', automations.pause_automation, ['POST']),
    ('/{automation_id}/resume', automations.resume_automation, ['POST']),
    ('/{automation_id}', automations.delete_automation, ['DELETE']),
    ('/{automation_id}/run', automations.run_automation, ['POST']),
    ('/{automation_id}/history', automations.automation_history, ['GET']),
):
    router.add_api_route(path, handler, methods=methods)


@router.post('/approvals/{approval_id}/approve')
def approve_listing(approval_id: str):
    raise HTTPException(409, 'Marketplace actions are not available on the web yet. This approval stays waiting.')


errors_router = errors.router
