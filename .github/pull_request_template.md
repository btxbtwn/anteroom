<!--
Closing keyword first: merging into main closes the issue. Use one keyword per
issue ("Closes #12, closes #14"). Every PR needs at least one; file the issue
first if there isn't one (Bug, Feature or Task form). Use "Refs #n" for an
issue this PR touches but does not finish.
-->
Closes #

## Summary

-

## Kind

- [ ] Bug fix: root cause in one sentence below
- [ ] Feature
- [ ] Refactor: no behavior change, nothing else mixed in
- [ ] Docs / CI / repo hygiene

Root cause (bug fixes):

## Component

- [ ] Anteroom web / Mac app
- [ ] Hosted API
- [ ] iPhone app
- [ ] Chrome extension
- [ ] Webhook relay
- [ ] `list-this` skills
- [ ] CI / repo hygiene

## Verification

- [ ] `./check.sh` passes (same checks as CI for what changed)
- [ ] Changes `anteroom/`: adds a `changelog.d/<branch-slug>.md` note, and does not touch `VERSION` or `CHANGELOG.md`
- [ ] A test fails without this change and passes with it (name it below), or why none can
- [ ] Visible change: screenshots from `./sandbox.sh` at desktop and phone sizes below
- [ ] Schema change: Alembic revision included and tested against the previous release's database

Tests and screenshots:

## Invariants

- [ ] Does not publish or delist listings, or skip the seller's explicit confirmation
- [ ] Does not commit API keys, photos, diagnostics, or private exports
- [ ] Anteroom still binds to `127.0.0.1`
