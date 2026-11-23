# Pull request review bots

Two reviewers look at every ready pull request from a branch in this
repository: **Codex** (ChatGPT's GitHub connector) and **Claude** (Claude Code
in GitHub Actions). Both follow [AGENTS.md › Review guidelines](../AGENTS.md#review-guidelines)
and run on the team's own subscriptions. They replace CodeRabbit and keep its
workflow.

| CodeRabbit did | Now | Where |
|---|---|---|
| Release notes in the PR description (`## Summary by CodeRabbit`) | `## Summary by Claude` upserted into the PR description on every review (and CodeRabbit's block removed) | `review_bot.py post-review` |
| Summary comment: walkthrough table, related issues and PRs, change type, merge risk, security risk | One sticky Claude summary comment, updated on every push | `claude-review.yml` |
| A review per push with inline findings (severity · category · effort, "Prompt to fix") and a "Prompt to fix all issues" | The same, as a `COMMENT` review from `github-actions[bot]`. A finding on a changed file but off the diff's lines becomes a file-level comment. One on a file the PR doesn't change goes in the review's "Outside diff range" section, which auto-fix doesn't track | `claude-review.yml` |
| Incremental reviews: only what changed since the last reviewed commit | The summary comment records the reviewed commit; the next push reviews only newer changes to the PR's files. A force-push or rebase that drops that commit starts a full review | `review_bot.py prepare-review` |
| "✅ Addressed in commit …" on fixed findings | Claude checks its open findings on each push, marks the fixed ones and resolves their threads. If GitHub refuses to resolve a thread, the summary still posts, notes which threads stay open and why, and the review check fails with that reason | `review_bot.py post-review` |
| Auto-fix PRs / "Bring PR to ready" replies to its own and Codex's findings | Claude answers every open Claude and Codex finding (fixed, already addressed, inapplicable, not fixed) with what it checked, pushes one fix commit, and posts the round's result | `claude-autofix.yml` |
| (new) Failed product CI on a pull request | When the `CI` workflow finishes red on a ready same-repo PR, Claude collects the failed job logs and runs the same auto-fix path (shared 3-round cap). Claude review / auto-fix job failures are not treated as CI | `claude-autofix.yml` `workflow_run` |
| Repair-round limit, "same blockers remain" | At most 3 pushed rounds per PR; the same open findings or CI jobs at the same commit are not retried | `review_bot.py autofix_decision` |
| `@coderabbitai` chat | `@claude` in any PR comment, review or review comment | `claude.yml` |
| Codex review | Unchanged: Codex reviews automatically and reads the same guidelines | Codex settings |

## How a pull request goes

1. Open or push: Codex and Claude review. Claude's review job runs
   `review_bot.py prepare-review`, then Claude reads the code and returns JSON
   (`review-schema.json`), then `post-review` posts it: seller-facing
   `## Summary by Claude` bullets go in the PR description, and the sticky
   comment keeps the walkthrough and risks. The workflow allows Claude only
   reading tools there (no edits, commits, pushes or comment commands); the
   job's token can still write the description and comments, so this is a tool
   restriction, not a missing credential.
2. Auto-fix runs right after Claude's review, again whenever Codex submits a
   review, and again when product `CI` finishes red on the PR. It first pauses
   auto-merge, as AGENTS.md requires before pushing to a queued PR, and never
   turns it back on: another review can arrive on the same commit at any time,
   so whoever has checked every finding queues the PR again. Claude verifies
   each open finding and each failed CI job (from the failed-job log), fixes
   the real ones, runs the narrowest tests, commits and pushes. Its push uses
   the Claude GitHub App token, so CI and the review run on the new commit (a
   push with the workflow token would start no CI), and the action's wrapper
   only pushes to the PR's branch. Claude cannot edit `.github/`,
   `anteroom/VERSION` or `CHANGELOG.md`. `post-autofix` then:
   - counts a finding or CI job as fixed only when Claude's own commit is on
     the branch;
   - flags a pushed change to a protected path as blocking;
   - replies in every finding's thread and posts the round's result (including
     any still-open CI job names).
3. The fix commit starts the next review and CI, which may lead to another
   auto-fix round, up to the limit.

Answered threads stay open for you to resolve, like CodeRabbit's. Auto-fix
never merges, never queues auto-merge and never publishes or delists
anything.

## Setup

1. Install the [Claude GitHub App](https://github.com/apps/claude) on this
   repository. Auto-fix pushes with its token.
2. On a machine signed in to the Claude subscription, run `claude setup-token`
   and store the token:
   ```bash
   gh secret set CLAUDE_CODE_OAUTH_TOKEN               # pull requests
   gh secret set CLAUDE_CODE_OAUTH_TOKEN --app dependabot  # Dependabot PRs
   ```
3. Codex: keep automatic reviews on for this repository in Codex's code
   review settings.
4. Once Claude has reviewed a pull request here, uninstall the CodeRabbit app
   (GitHub → Settings → Applications → CodeRabbit) and cancel its plan.

## Differences from CodeRabbit

- No bundled linters (CodeRabbit ran tools such as ast-grep). CI already runs
  ruff, basedpyright, oxlint and the rest.
- Claude answers replies in its threads only when they mention `@claude`.
- No learned preferences. Put lasting review rules in AGENTS.md › Review
  guidelines, which both bots read.
- Pull requests from forks are not reviewed: GitHub gives them no secrets.
- Reviews and fixes use the subscription's usage limits, shared with
  interactive Claude Code and Codex sessions.

## Changing it

Run `python3 .github/review-bot/test_review_bot.py` after editing
`review_bot.py`; CI runs it in **Repo checks**. The prompts are
`.github/review-bot/review-prompt.md` and `autofix-prompt.md`.
