## Summary

-

## Kind

- [ ] Bug fix: root cause in one sentence below
- [ ] Feature
- [ ] Refactor: no behavior change, nothing else mixed in
- [ ] Docs / CI / repo hygiene

Root cause (bug fixes):

## Component

- [ ] Anteroom
- [ ] Chrome extension
- [ ] Webhook relay
- [ ] `list-this` skills
- [ ] CI / repo hygiene

## Verification

- [ ] `./check.sh` passes (same checks as CI for what changed)
- [ ] A test fails without this change and passes with it (name it below), or why none can
- [ ] Visible change: screenshots from `./sandbox.sh` at desktop and phone sizes below

Tests and screenshots:

## Invariants

- [ ] Does not publish listings or skip human approval
- [ ] Does not commit API keys, photos, diagnostics, or private exports
- [ ] Anteroom still binds to `127.0.0.1`
