"""Automations on the web, using the desktop handlers and services.

General automation tasks use the seller's own rows and queue marketplace
actions for approval. The separate auto-relist run uses the seller's opt-in
marketplace rules directly.
"""
from fastapi import APIRouter, Depends, HTTPException

from anteroom.routes import automations, errors
from anteroom.services.hosted_limits import limit_seller_submissions

router = APIRouter(prefix='/api/automations', tags=['automations'])
_RATE_LIMITED_PATHS = {'/draft', '/auto-relist/run', '/{automation_id}/run'}
for path, handler, methods in (
    ('', automations.list_automations, ['GET']),
    ('/draft', automations.draft_automation, ['POST']),
    ('/preview', automations.preview_automation, ['POST']),
    ('/approvals', automations.list_approvals, ['GET']),
    ('/scheduled-drafts', automations.list_scheduled_drafts, ['GET']),
    ('/scheduled-drafts/logs', automations.list_scheduled_draft_logs, ['GET']),
    ('/scheduled-drafts/bulk-schedule', automations.bulk_schedule_drafts, ['POST']),
    ('/scheduled-drafts/bulk-unschedule', automations.bulk_unschedule_drafts, ['POST']),
    ('/auto-relist', automations.list_auto_relist_rules, ['GET']),
    ('/auto-relist/logs', automations.list_auto_relist_logs, ['GET']),
    ('/auto-relist/{marketplace}', automations.save_auto_relist_rule, ['PUT']),
    ('/auto-relist/run', automations.run_auto_relist_now, ['POST']),
    ('', automations.create_automation, ['POST']),
    ('/{automation_id}', automations.update_automation, ['PATCH']),
    ('/{automation_id}/pause', automations.pause_automation, ['POST']),
    ('/{automation_id}/resume', automations.resume_automation, ['POST']),
    ('/{automation_id}', automations.delete_automation, ['DELETE']),
    ('/{automation_id}/run', automations.run_automation, ['POST']),
    ('/{automation_id}/history', automations.automation_history, ['GET']),
):
    deps = [Depends(limit_seller_submissions)] if path in _RATE_LIMITED_PATHS else None
    router.add_api_route(path, handler, methods=methods, dependencies=deps)


@router.post('/approvals/{approval_id}/approve')
def approve_listing(approval_id: str):
    raise HTTPException(409, 'Marketplace actions are not available on the web yet. This approval stays waiting.')


errors_router = errors.router
