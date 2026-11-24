"""Tests for review_bot.py's formatting and decision logic. Run:
python3 .github/review-bot/test_review_bot.py"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import release_autofix as release  # noqa: E402
import review_bot as bot  # noqa: E402

DIFF = """diff --git a/app.py b/app.py
index 1..2 100644
--- a/app.py
+++ b/app.py
@@ -1,4 +1,5 @@
 import os
-x = 1
+x = 2
+y = 3
 
--- not a header, a removed line
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-print(1)
"""

FINDING = {
    "path": "app.py", "line": 3, "start_line": None, "severity": "Major",
    "category": "Functional Correctness", "effort": "Quick win",
    "title": "Keep y in range.", "body": "It overflows.", "fix_prompt": "Clamp y.",
}


def thread(root_login, root_body, replies=(), resolved=False):
    comments = [{"databaseId": 7, "author": {"login": root_login}, "body": root_body}]
    comments += [{"databaseId": 8 + i, "author": {"login": "github-actions"}, "body": b} for i, b in enumerate(replies)]
    return {"id": "T1", "isResolved": resolved, "path": "app.py", "line": 3, "comments": {"nodes": comments}}


class ReleaseAutofixTests(unittest.TestCase):
    def source(self, workflow, event):
        return {
            "path": workflow, "event": event, "head_branch": "main",
            "head_repository": {"full_name": "owner/repo"}, "run_attempt": 2,
            "status": "completed", "conclusion": "failure",
        }

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"})
    def test_eligibility_checks_origin_event_and_current_attempt(self):
        mac = self.source(release.MAC, "push")
        deploy = self.source(release.DEPLOY, "workflow_run")
        self.assertTrue(release.eligible(mac, 2))
        self.assertTrue(release.eligible(deploy, 2))
        self.assertFalse(release.eligible(deploy, 1))
        for change in (
            {"event": "pull_request"},
            {"head_branch": "feature"},
            {"head_repository": {"full_name": "fork/repo"}},
            {"status": "in_progress"},
            {"conclusion": "success"},
        ):
            self.assertFalse(release.eligible({**mac, **change}, 2), change)

    def test_only_failed_safe_steps_provide_detailed_logs(self):
        build = "Install dependencies and build frontend"
        jobs = [{
            "name": "package", "conclusion": "failure",
            "steps": [{"name": name, "conclusion": "failure"} for name in (build, "Package app")],
        }]
        log = "\n".join((
            f"package\t{build}\tfrontend build error",
            "package\tPackage app\tNSURLErrorDomain Code=-1001 private submission metadata",
            "package\tImport signing certificates\tprivate signing metadata",
            f"another job\t{build}\tlog from a different job",
        ))
        evidence, safe_log = release.failure_evidence(release.MAC, jobs, log)
        self.assertEqual(safe_log, f"package\t{build}\tfrontend build error")
        self.assertIn("timed out", evidence["diagnostics"][0])
        self.assertNotIn("metadata", str(evidence))

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"})
    def test_prepare_uses_the_selected_hosted_revision(self):
        selected, trigger = "a" * 40, "b" * 40
        source = {**self.source(release.DEPLOY, "workflow_run"), "head_sha": trigger}
        jobs = [{"id": 123, "name": "Migrate and deploy", "conclusion": "failure"}]
        notes = [{"title": "Deploy target", "message": selected}]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"RELEASE_REVIEW_DIR": tmp, "SOURCE_RUN": "10", "SOURCE_ATTEMPT": "2"},
        ), patch.object(release, "api", side_effect=[source, {"jobs": jobs}, notes]) as api, patch.object(
            release, "git", return_value=trigger,
        ), patch.object(release, "is_ancestor", return_value=True) as ancestor, patch.object(
            release, "repair_pr", return_value="",
        ), patch.object(release, "run", return_value="") as run, patch.object(release, "set_output") as output:
            release.prepare()
            context = json.loads(Path(tmp, "context.json").read_text())
        self.assertEqual(context["failed_revision"], selected)
        self.assertTrue(context["repair_branch"].endswith(selected))
        self.assertIn("/attempts/2/jobs", api.call_args_list[1].args[0])
        self.assertIn("2", run.call_args.args)
        ancestor.assert_called_once_with(selected, trigger)
        output.assert_called_once_with(skip="false", can_repair="true")

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"})
    def test_publish_pushes_a_new_branch_and_opens_a_linked_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout, remote = root / "checkout", root / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(checkout)], check=True, capture_output=True)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(checkout), *args], text=True, stderr=subprocess.PIPE)

            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            git("remote", "add", "origin", str(remote))
            git("commit", "--allow-empty", "-m", "Main")
            git("push", "origin", "main")
            base = git("rev-parse", "HEAD").strip()
            branch = f"claude/release-fix/studio-macos/{base}"
            url = "https://github.com/owner/repo/pull/8"
            context = {
                "repair_branch": branch, "base": base, "run_id": 10, "attempt": 2,
                "url": "https://github.com/owner/repo/actions/runs/10/attempts/2",
                "pipeline": "Studio macOS", "failed_revision": base,
            }
            (checkout / ".github").mkdir()
            protected = checkout / ".github" / "protected.yml"
            protected.write_text("protected")
            (checkout / "fix.py").write_text("pass\n")
            (root / "context.json").write_text(json.dumps(context))
            (root / "report.md").write_text("Verified by a sandbox check.\n")
            with patch.dict(os.environ, {"RELEASE_REVIEW_DIR": tmp}), patch.object(
                release, "git", git,
            ), patch.object(release, "api", side_effect=[
                self.source(release.MAC, "push"), self.source(release.MAC, "push"),
                {"number": 7}, {"html_url": url},
            ]) as api, patch.object(release, "repair_pr", return_value=""), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "protected files"):
                    release.publish()
                self.assertEqual(git("ls-remote", "--heads", "origin").splitlines(), [f"{base}\trefs/heads/main"])
                protected.unlink()
                release.publish()
            refs = git("ls-remote", "--heads", "origin")
            self.assertIn(f"{base}\trefs/heads/main", refs)
            self.assertIn(f"refs/heads/{branch}", refs)
            payload = api.call_args.args[2]
            self.assertEqual(payload["base"], "main")
            self.assertEqual(payload["head"], branch)
            self.assertTrue(payload["body"].startswith("Closes #7\n"))
            self.assertEqual((root / "repair-pr.txt").read_text(), url)


class DiffTests(unittest.TestCase):
    def test_commentable_lines_follow_new_side(self):
        lines = bot.commentable_lines(DIFF)
        self.assertEqual(lines["app.py"], {1, 2, 3, 4})
        self.assertNotIn("gone.py", lines)

    def test_findings_off_the_diff_become_file_comments(self):
        comment = bot.review_comment(FINDING | {"line": 40}, bot.commentable_lines(DIFF))
        self.assertEqual(comment["subject_type"], "file")
        self.assertNotIn("line", comment)
        self.assertIn(bot.FINDING_MARK, comment["body"])

    def test_ranges_only_when_every_line_is_in_the_diff(self):
        allowed = bot.commentable_lines(DIFF)
        self.assertEqual(bot.review_comment(FINDING | {"start_line": 2}, allowed)["start_line"], 2)
        self.assertNotIn("start_line", bot.review_comment(FINDING | {"start_line": 1, "line": 9}, allowed))


class ReviewScopeTests(unittest.TestCase):
    def test_first_review_covers_the_whole_pr(self):
        self.assertEqual(bot.review_scope(["a.py", "b.py"], None, ["a.py"]), (["a.py", "b.py"], []))

    def test_incremental_review_keeps_only_this_prs_new_changes(self):
        # c.py changed only through a merge from the base branch.
        self.assertEqual(bot.review_scope(["a.py", "b.py"], ["b.py", "c.py"], []), (["b.py"], []))

    def test_file_restored_to_base_is_reassessed_for_its_open_findings(self):
        files, restored = bot.review_scope(["b.py"], ["a.py", "c.py"], ["a.py"])
        self.assertEqual((files, restored), ([], ["a.py"]))

    def test_base_merge_alone_reviews_nothing(self):
        self.assertEqual(bot.review_scope(["b.py"], ["c.py"], ["a.py"]), ([], []))


class RenderTests(unittest.TestCase):
    def test_marker_round_trip(self):
        body = "hi\n" + bot.marker(bot.REVIEW_STATE, {"reviewed_sha": "abc"})
        self.assertEqual(bot.read_marker(bot.REVIEW_STATE, body), {"reviewed_sha": "abc"})
        self.assertIsNone(bot.read_marker(bot.AUTOFIX_STATE, body))

    def test_fence_outgrows_backticks_in_the_prompt(self):
        self.assertTrue(bot.fence("use ```py``` here").startswith("````\n"))

    def test_finding_has_mark_severity_and_prompt(self):
        body = bot.render_finding(FINDING)
        self.assertIn(bot.FINDING_MARK, body)
        self.assertIn("🟠 Major", body)
        self.assertIn("Review comment at @app.py at line 3:\nClamp y.", body)

    def test_review_body_counts_threaded_findings(self):
        ctx = {"head": "h" * 40, "merge_base": "m" * 40, "since": "s" * 40, "files": ["app.py"]}
        body = bot.render_review_body([FINDING], ctx)
        self.assertTrue(body.startswith("**Actionable comments posted: 1**"))
        self.assertIn(f"between {'s' * 40} and {'h' * 40}", body)
        self.assertNotIn("Outside diff range", body)

    def test_summary_escapes_table_cells_and_records_state(self):
        data = {
            "change_type": "Bug fix",
            "summary": [{"category": "Bug Fixes", "bullets": ["Stopped the leak."]}],
            "walkthrough": [{"heading": "Fix", "rows": [{"layer": "A|B", "files": ["app.py"], "summary": "x\ny"}]}],
            "related_issues": [{"number": 5, "reason": "same bug"}], "related_prs": [],
            "merge_risk": {"level": "Low", "explanation": "small"},
            "security_risk": {"level": "Minimal", "explanation": "none", "concerns": []},
        }
        body = bot.render_summary(data, {"head": "abcdef1234"}, posted=0, review_url=None)
        self.assertEqual(bot.read_marker(bot.REVIEW_STATE, body), {"reviewed_sha": "abcdef1234"})
        self.assertIn("|**A\\|B** <br> `app.py`|x y|", body)
        self.assertIn("- #5: same bug", body)
        self.assertIn("No actionable comments", body)
        self.assertIn("up to <code>abcdef1</code>", body)
        self.assertIn("No architecture-level concerns identified.", body)

    def test_pr_summary_matches_coderabbit_release_notes_shape(self):
        data = {
            "summary": [
                {"category": "Bug Fixes", "bullets": ["Fixed Run due relists.", "Skipped open offers."]},
                {"category": "New Features", "bullets": ["Account imports include pricing."]},
                {"category": "Tests", "bullets": ["  ", ""]},  # skipped: no real bullets
            ]
        }
        block = bot.render_pr_summary(data)
        self.assertTrue(block.startswith(bot.PR_SUMMARY_START))
        self.assertIn("## Summary by Claude", block)
        self.assertIn("* **Bug Fixes**\n  * Fixed Run due relists.\n  * Skipped open offers.", block)
        self.assertIn("* **New Features**\n  * Account imports include pricing.", block)
        self.assertNotIn("**Tests**", block)
        self.assertTrue(block.endswith(bot.PR_SUMMARY_END))
        self.assertEqual(bot.render_pr_summary({"summary": []}), "")

    def test_merge_pr_body_replaces_claude_and_strips_coderabbit(self):
        author = "Closes #1\n\n## Summary\n\n- Fixed it.\n"
        prior = author + "\n<!-- claude-summary:v1 -->\n\n## Summary by Claude\n\n* **Chores**\n  * Old.\n<!-- end of claude-summary -->\n"
        rabbit = (
            author
            + "\n<!-- coderabbit-ai-generated:v1 -->\n\n## Summary by CodeRabbit\n\n"
            + "* **Bug Fixes**\n  * Old rabbit.\n"
            + "<!-- end of auto-generated comment: release notes by coderabbit.ai -->\n"
        )
        fresh = bot.render_pr_summary({"summary": [{"category": "Bug Fixes", "bullets": ["New."]}]})
        merged = bot.merge_pr_body(prior, fresh)
        self.assertTrue(merged.startswith(author.rstrip()))
        self.assertIn("## Summary by Claude", merged)
        self.assertIn("* **Bug Fixes**\n  * New.", merged)
        self.assertNotIn("Old.", merged)
        self.assertEqual(merged.count(bot.PR_SUMMARY_START), 1)
        stripped = bot.merge_pr_body(rabbit, fresh)
        self.assertNotIn("Summary by CodeRabbit", stripped)
        self.assertNotIn("coderabbit-ai-generated", stripped)
        self.assertIn("* **Bug Fixes**\n  * New.", stripped)
        self.assertEqual(bot.merge_pr_body(author, ""), author if author.endswith("\n") else author + "\n")

    def test_merge_pr_body_preserves_paragraphs_around_removed_summary(self):
        fresh = bot.render_pr_summary({"summary": [{"category": "Bug Fixes", "bullets": ["New."]}]})
        markers = (
            (bot.PR_SUMMARY_START, bot.PR_SUMMARY_END),
            (
                "<!-- coderabbit-ai-generated:v1 -->",
                "<!-- end of auto-generated comment: release notes by coderabbit.ai -->",
            ),
        )
        for start, end in markers:
            with self.subTest(start=start):
                body = f"Fixed it.\n\n{start}\nOld summary.\n{end}\n\nScreenshots follow."
                self.assertEqual(
                    bot.merge_pr_body(body, fresh),
                    f"Fixed it.\n\nScreenshots follow.\n\n{fresh}\n",
                )

    def test_addressed_is_added_once(self):
        once = bot.addressed_body("finding", "1234567890")
        self.assertTrue(once.endswith("✅ Addressed in commit 1234567"))
        self.assertEqual(bot.addressed_body(once, "999"), once)


class PostReviewTests(unittest.TestCase):
    def test_only_live_pr_paths_get_findings_and_each_finding_gets_a_thread(self):
        findings = [FINDING | {"path": path} for path in ("app.py", "mode.py", "gone.py", "unchanged.py")]
        with tempfile.TemporaryDirectory() as temp:
            ctx = Path(temp)
            (ctx / "context.json").write_text(json.dumps({
                "pr": 1, "head": "head", "merge_base": "base", "since": None,
                "files": ["app.py", "mode.py", "gone.py", "unchanged.py"],
            }))
            (ctx / "pr.diff").write_text(DIFF + "diff --git a/mode.py b/mode.py\nold mode 100644\nnew mode 100755\n")
            (ctx / "open-findings.json").write_text("[]")
            with (
                patch.dict(bot.os.environ, {"REVIEW_OUTPUT": json.dumps({"findings": findings})}),
                patch.object(bot.subprocess, "check_output", return_value=b"app.py\0mode.py\0") as diff_paths,
                patch.object(bot, "repo", return_value="owner/repo"),
                patch.object(bot, "api", return_value={}) as api,
                patch.object(bot, "bot_issue_comments", return_value=[]),
                patch.object(bot, "render_summary") as summary,
                patch.object(bot, "upsert_pr_body_summary") as upsert,
            ):
                bot.post_review(SimpleNamespace(context=temp))

        review, file_comment, _ = [call.args[2] for call in api.call_args_list]
        self.assertEqual([c["path"] for c in review["comments"]], ["app.py"])
        self.assertEqual((file_comment["path"], file_comment["subject_type"]), ("mode.py", "file"))
        self.assertIn(bot.FINDING_MARK, file_comment["body"])
        self.assertIn("Outside diff range comments (2)", review["body"])
        self.assertIn("`unchanged.py` line 3", review["body"])
        self.assertNotIn(bot.FINDING_MARK, review["body"])
        self.assertEqual(summary.call_args.kwargs["posted"], 2)
        upsert.assert_called_once()
        self.assertIn("--diff-filter=d", diff_paths.call_args.args[0])

    # Arbitrary, non-secret text a failing command might print. None of it may
    # leave the process: not in comments, annotations, exit messages or state.
    PROBE = "PROBE-7f3a <b>markup</b> /home/runner/path line\nsecond line"

    def _post_review_with_addressed(self, temp, graphql, *, addressed=(11, 22), pending=(), resolved=(),
                                    pending_categories=("permission denied",)):
        """Run post_review on two finding threads; return API calls, upsert, exit code and stderr."""
        ctx = Path(temp)
        (ctx / "context.json").write_text(json.dumps({
            "pr": 1, "head": "head", "merge_base": "base", "since": None, "files": [],
            "pending_unresolved": list(pending), "pending_categories": list(pending_categories) if pending else [],
        }))
        (ctx / "pr.diff").write_text("")
        (ctx / "open-findings.json").write_text(json.dumps([{"comment_id": 11}, {"comment_id": 22}]))
        threads = [
            {"id": f"T{cid}", "isResolved": f"T{cid}" in resolved,
             "comments": {"nodes": [{"databaseId": cid, "body": "finding"}]}}
            for cid in (11, 22)
        ]
        output = {
            "change_type": "Bug fix", "summary": [], "walkthrough": [], "related_issues": [], "related_prs": [],
            "merge_risk": {"level": "Low", "explanation": "small"},
            "security_risk": {"level": "Minimal", "explanation": "none", "concerns": []},
            "findings": [], "addressed": [{"comment_id": cid, "commit": f"c{cid}"} for cid in addressed],
        }
        stderr, stdout = io.StringIO(), io.StringIO()
        with (
            patch.dict(bot.os.environ, {"REVIEW_OUTPUT": json.dumps(output)}),
            patch.object(bot.subprocess, "check_output", return_value=b""),
            patch.object(bot, "repo", return_value="owner/repo"),
            patch.object(bot, "api", return_value={}) as api,
            patch.object(bot, "review_threads", return_value=threads),
            patch.object(bot, "graphql", side_effect=graphql),
            patch.object(bot, "bot_issue_comments", return_value=[]),
            patch.object(bot, "upsert_pr_body_summary") as upsert,
            contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout),
        ):
            try:
                bot.post_review(SimpleNamespace(context=temp))
                exit_reason = None
            except SystemExit as stop:
                exit_reason = stop.code
        return api.call_args_list, upsert, exit_reason, stderr.getvalue() + stdout.getvalue()

    def _refused(self, stderr):
        """resolve_thread failing the way run() reports a refused gh call."""
        def refuse(query, **variables):
            with patch.object(bot.subprocess, "run", return_value=bot.subprocess.CompletedProcess(
                    ["gh"], 1, stdout="", stderr=stderr)):
                bot.run("gh", "api", "graphql", "--input", "-", "PROBE-ARGUMENT-TEXT", input="PROBE-INPUT-TEXT")
        return refuse

    def test_a_failed_thread_resolve_still_posts_everything_then_fails_the_review(self):
        refuse = self._refused(f"gh: {self.PROBE} (HTTP 403)")
        with tempfile.TemporaryDirectory() as temp:
            calls, upsert, exit_reason, logs = self._post_review_with_addressed(temp, refuse)
        patched = [c.args[0] for c in calls if c.args[1] == "PATCH"]
        # Both findings are marked addressed, even though neither thread could be resolved.
        self.assertEqual(patched, ["repos/owner/repo/pulls/comments/11", "repos/owner/repo/pulls/comments/22"])
        summary = [c.args[2]["body"] for c in calls if c.args[0] == "repos/owner/repo/issues/1/comments"]
        self.assertEqual(len(summary), 1)
        # The reviewed commit is recorded with the open threads and their category only.
        self.assertEqual(
            bot.read_marker(bot.REVIEW_STATE, summary[0]),
            {"reviewed_sha": "head", "unresolved_threads": ["T11", "T22"],
             "unresolved_categories": ["permission denied"]},
        )
        self.assertIn("Could not resolve 2 addressed thread(s): permission denied", summary[0])
        upsert.assert_called_once()
        # The job still fails, with the category, instead of passing as a clean review.
        self.assertEqual(exit_reason, "Could not resolve 2 addressed thread(s): permission denied")
        for text in (summary[0], exit_reason, logs):
            for probe in ("PROBE", "markup", "/home/runner", "second line"):
                self.assertNotIn(probe, text)

    def test_resolved_threads_pass_without_a_failure_note(self):
        with tempfile.TemporaryDirectory() as temp:
            calls, upsert, exit_reason, _ = self._post_review_with_addressed(temp, lambda query, **v: {})
        self.assertIsNone(exit_reason)
        summary = [c.args[2]["body"] for c in calls if c.args[0] == "repos/owner/repo/issues/1/comments"]
        self.assertNotIn("Could not resolve", summary[0])
        self.assertEqual(bot.read_marker(bot.REVIEW_STATE, summary[0]), {"reviewed_sha": "head"})
        upsert.assert_called_once()

    def test_threads_an_earlier_run_could_not_resolve_keep_the_review_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            calls, _, exit_reason, _ = self._post_review_with_addressed(
                temp, lambda query, **v: {}, addressed=(), pending=["T11"])
        self.assertEqual(exit_reason, "Could not resolve 1 addressed thread(s): permission denied")
        summary = [c.args[2]["body"] for c in calls if c.args[0] == "repos/owner/repo/issues/1/comments"]
        self.assertEqual(bot.read_marker(bot.REVIEW_STATE, summary[0])["unresolved_threads"], ["T11"])
        # A category that is not on the allowlist is reported as unknown, never echoed.
        with tempfile.TemporaryDirectory() as temp:
            _, _, exit_reason, _ = self._post_review_with_addressed(
                temp, lambda query, **v: {}, addressed=(), pending=["T11"], pending_categories=[self.PROBE])
        self.assertEqual(exit_reason, "Could not resolve 1 addressed thread(s): unknown")
        # Once a person resolves it, the next review passes.
        with tempfile.TemporaryDirectory() as temp:
            _, _, exit_reason, _ = self._post_review_with_addressed(
                temp, lambda query, **v: {}, addressed=(), pending=["T11"], resolved=["T11"])
        self.assertIsNone(exit_reason)

    def _prepare_same_head(self, state, threads):
        outputs, logs = {}, io.StringIO()
        with tempfile.TemporaryDirectory() as out, (
            patch.object(bot, "git", side_effect=lambda *a: "head\n" if a[0] == "rev-parse" else "base\n")
        ), (
            patch.object(bot, "bot_issue_comments", return_value=[({"id": 1}, state)])
        ), (
            patch.object(bot, "review_threads", return_value=threads)
        ), patch.object(bot, "set_output", side_effect=outputs.update), contextlib.redirect_stdout(logs):
            try:
                bot.prepare_review(SimpleNamespace(pr=1, base="main", out=out))
            except SystemExit as stop:
                return outputs, stop.code, logs.getvalue()
        return outputs, None, logs.getvalue()

    def test_rerunning_the_same_commit_does_not_pass_while_threads_stay_unresolved(self):
        state = {"reviewed_sha": "head", "unresolved_threads": ["T11"], "unresolved_categories": ["rate limited"]}
        outputs, exit_reason, _ = self._prepare_same_head(state, [{"id": "T11", "isResolved": False}])
        self.assertEqual(outputs, {"skip": "true"})
        self.assertEqual(exit_reason, "Could not resolve 1 addressed thread(s): rate limited. "
                                      "Resolve them by hand after checking.")
        outputs, exit_reason, _ = self._prepare_same_head(state, [{"id": "T11", "isResolved": True}])
        self.assertEqual((outputs, exit_reason), ({"skip": "true"}, None))
        outputs, exit_reason, _ = self._prepare_same_head({"reviewed_sha": "head"}, [])
        self.assertEqual((outputs, exit_reason), ({"skip": "true"}, None))
        # An edited marker cannot put arbitrary text into the annotation or exit message.
        tampered = {"reviewed_sha": "head", "unresolved_threads": ["T11"], "unresolved_categories": [self.PROBE]}
        _, exit_reason, logs = self._prepare_same_head(tampered, [{"id": "T11", "isResolved": False}])
        self.assertIn("Could not resolve 1 addressed thread(s): unknown", exit_reason)
        self.assertNotIn("PROBE", exit_reason + logs)

    def test_failures_are_classified_by_status_and_type_never_echoed(self):
        cases = [
            ("", f"gh: {self.PROBE} (HTTP 403)", "permission denied"),
            ("", f"gh: {self.PROBE} (HTTP 401)", "permission denied"),
            ("", f"gh: {self.PROBE} (HTTP 404)", "not found"),
            ("", f"gh: {self.PROBE} (HTTP 429)", "rate limited"),
            ("", "gh: API rate limit exceeded for installation (HTTP 403)", "rate limited"),
            (json.dumps({"errors": [{"type": "FORBIDDEN", "message": self.PROBE}]}), f"gh: {self.PROBE}",
             "permission denied"),
            (json.dumps({"errors": [{"type": "NOT_FOUND", "message": self.PROBE}]}), "", "not found"),
            ("not json " + self.PROBE, f"gh: {self.PROBE} (HTTP 500)", "unknown"),
            ("", self.PROBE, "unknown"),
        ]
        for stdout, stderr, category in cases:
            failed = bot.subprocess.CompletedProcess(["gh"], 1, stdout=stdout, stderr=stderr)
            with self.subTest(stderr=stderr[:40]), patch.object(bot.subprocess, "run", return_value=failed), \
                    self.assertRaises(bot.CommandError) as raised:
                bot.run("gh", "api", "PROBE-ARGUMENT-TEXT", "--input", "-", input="PROBE-INPUT-TEXT")
            error = raised.exception
            self.assertEqual(error.category, category)
            self.assertEqual(error.returncode, 1)
            self.assertIsInstance(error, bot.subprocess.CalledProcessError)
            shown = str(error) + repr(error.cmd) + repr(error.stderr) + repr(error.output) + "".join(
                traceback.format_exception(error))
            self.assertEqual(str(error), f"GitHub CLI call failed ({category}, exit status 1)")
            for probe in ("PROBE", "markup", "/home/runner", "second line", "ARGUMENT-TEXT", "INPUT-TEXT"):
                self.assertNotIn(probe, shown)
        failed = bot.subprocess.CompletedProcess(["git"], 128, stdout="", stderr=f"fatal: {self.PROBE}")
        with patch.object(bot.subprocess, "run", return_value=failed), self.assertRaises(bot.CommandError) as raised:
            bot.run("git", "PROBE-ARGUMENT-TEXT")
        self.assertEqual(str(raised.exception), "git command failed (unknown, exit status 128)")

    def test_graphql_errors_report_only_their_category(self):
        for errors, category in (
            ([{"type": "FORBIDDEN", "message": self.PROBE}], "permission denied"),
            ([{"type": "RATE_LIMITED", "message": self.PROBE}], "rate limited"),
            ([{"type": self.PROBE, "message": self.PROBE}], "unknown"),
            ([self.PROBE], "unknown"),
        ):
            with patch.object(bot, "run", return_value=json.dumps({"data": None, "errors": errors})), \
                    self.assertRaises(bot.GraphQLError) as raised:
                bot.graphql("mutation { x }")
            self.assertEqual(raised.exception.category, category)
            self.assertEqual(str(raised.exception), f"GitHub GraphQL request failed ({category})")

    def test_upsert_pr_body_summary_patches_when_body_changes(self):
        data = {"summary": [{"category": "Bug Fixes", "bullets": ["Fixed it."]}]}
        calls = []

        def fake_api(path, method="GET", body=None):
            calls.append((path, method, body))
            if method == "GET":
                return {"body": "Closes #9\n"}
            return {}

        with patch.object(bot, "repo", return_value="owner/repo"), patch.object(bot, "api", side_effect=fake_api):
            bot.upsert_pr_body_summary(9, data)

        self.assertEqual(calls[0], ("repos/owner/repo/pulls/9", "GET", None))
        self.assertEqual(calls[1][0:2], ("repos/owner/repo/pulls/9", "PATCH"))
        self.assertIn("## Summary by Claude", calls[1][2]["body"])
        self.assertIn("Closes #9", calls[1][2]["body"])

        already = bot.merge_pr_body("Closes #9\n", bot.render_pr_summary(data))
        calls.clear()

        def fake_api_same(path, method="GET", body=None):
            calls.append((path, method, body))
            if method == "GET":
                return {"body": already}
            return {}

        with patch.object(bot, "repo", return_value="owner/repo"), patch.object(bot, "api", side_effect=fake_api_same):
            bot.upsert_pr_body_summary(9, data)
        self.assertEqual(calls, [("repos/owner/repo/pulls/9", "GET", None)])


class AutofixTests(unittest.TestCase):
    def test_open_findings_include_claude_and_codex_only(self):
        threads = [
            thread("github-actions[bot]", bot.FINDING_MARK + " bug"),
            thread("chatgpt-codex-connector", "P1 bug"),  # GraphQL drops "[bot]"
            thread("someone", "question"),
            thread("github-actions[bot]", "summary without mark"),
            thread("chatgpt-codex-connector[bot]", "done", resolved=True),
        ]
        found = bot.open_autofix_findings(threads, "head1")
        self.assertEqual([f["reviewer"] for f in found], ["claude", "codex"])

    def test_answered_findings_are_skipped_and_failed_ones_retried_on_new_heads(self):
        answered = bot.marker(bot.AUTOFIX_REPLY, {"head": "h0", "outcome": "inapplicable"})
        failed = bot.marker(bot.AUTOFIX_REPLY, {"head": "h0", "outcome": "not_fixed"})
        self.assertEqual(bot.open_autofix_findings([thread(bot.CODEX_LOGIN, "x", [answered])], "h1"), [])
        self.assertEqual(bot.open_autofix_findings([thread(bot.CODEX_LOGIN, "x", [failed])], "h0"), [])
        self.assertEqual(len(bot.open_autofix_findings([thread(bot.CODEX_LOGIN, "x", [failed])], "h1")), 1)

    def test_decision(self):
        self.assertEqual(bot.autofix_decision([], [], "h"), ("skip", None))
        self.assertEqual(bot.autofix_decision([], [1], "h"), ("run", None))
        self.assertEqual(bot.autofix_decision([], [], "h", ci_open=["Studio frontend"]), ("run", None))
        self.assertEqual(bot.autofix_decision([], [], "h", conflict_files=["app.py"]), ("run", None))
        self.assertEqual(bot.autofix_decision([], [], "h", merge_clean=True), ("run", None))
        pushed = [{"head": f"h{i}", "pushed": True} for i in range(bot.MAX_ROUNDS)]
        self.assertEqual(bot.autofix_decision(pushed, [1], "h9"), ("skip", "max_rounds"))
        self.assertEqual(bot.autofix_decision(pushed, [], "h9", ci_open=["Studio frontend"]), ("skip", "max_rounds"))
        self.assertEqual(
            bot.autofix_decision(pushed, [], "h9", conflict_files=["app.py"]),
            ("skip", "max_rounds"),
        )
        stopped = pushed + [{"head": "h9", "reason": "max_rounds"}]
        self.assertEqual(bot.autofix_decision(stopped, [1], "h9"), ("skip", None))
        tried = [{"head": "h", "pushed": False, "open": [1, 2]}]
        self.assertEqual(bot.autofix_decision(tried, [1], "h"), ("skip", "same_blockers"))
        self.assertEqual(bot.autofix_decision(tried, [1, 3], "h"), ("run", None))
        self.assertEqual(bot.autofix_decision(tried, [1], "h2"), ("run", None))
        self.assertEqual(
            bot.autofix_decision(tried, [1], "h", conflict_files=["app.py"]),
            ("run", None),
        )
        tried_conflict = [{"head": "h", "pushed": False, "open": [], "conflict_files": ["app.py"]}]
        self.assertEqual(
            bot.autofix_decision(tried_conflict, [], "h", conflict_files=["app.py"]),
            ("skip", "same_blockers"),
        )
        # New CI failures at the same head still run even if findings shrank.
        self.assertEqual(
            bot.autofix_decision(tried, [1], "h", ci_open=["Studio frontend"]),
            ("run", None),
        )
        tried_ci = [{"head": "h", "pushed": False, "open": [], "ci_open": ["Studio frontend"]}]
        self.assertEqual(
            bot.autofix_decision(tried_ci, [], "h", ci_open=["Studio frontend"]),
            ("skip", "same_blockers"),
        )

    def test_failed_ci_jobs_skip_aggregate_gate(self):
        jobs = [
            {"name": "Studio frontend", "conclusion": "failure", "id": 1, "html_url": "https://example/1"},
            {"name": "CI", "conclusion": "failure", "id": 2, "html_url": "https://example/2"},
            {"name": "Extension", "conclusion": "success", "id": 3, "html_url": "https://example/3"},
            {"name": "", "conclusion": "failure", "id": 4},
        ]
        self.assertEqual(
            bot.failed_ci_jobs(jobs),
            [{"name": "Studio frontend", "id": 1, "html_url": "https://example/1"}],
        )
        self.assertTrue(bot.truncate_log("x" * 10, limit=4).startswith("...(truncated"))
        self.assertEqual(bot.truncate_log("short", limit=100), "short")

    def test_lockfile_conflicts_skip_the_dependency_install(self):
        self.assertTrue(bot.lockfile_conflict(["app.py", "anteroom/package-lock.json"]))
        self.assertTrue(bot.lockfile_conflict(["anteroom/uv.lock"]))
        self.assertFalse(bot.lockfile_conflict(["anteroom/package.json", "uv.lock"]))
        self.assertFalse(bot.lockfile_conflict([]))

    def test_protected_conflict_note_posts_once_per_head(self):
        files = [".github/workflows/ci.yml"]
        self.assertTrue(bot.protected_conflict_note_needed([], "h", files))
        noted = [{"head": "h", "reason": "protected_conflicts", "conflict_files": files}]
        self.assertFalse(bot.protected_conflict_note_needed(noted, "h", files))
        self.assertTrue(bot.protected_conflict_note_needed(noted, "h2", files))
        self.assertTrue(bot.protected_conflict_note_needed(noted, "h", files + ["CHANGELOG.md"]))
        later = noted + [{"head": "h", "reason": "blocked", "conflict_files": files}]
        self.assertTrue(bot.protected_conflict_note_needed(later, "h", files))

    def test_reply_and_result_text(self):
        reply = bot.render_autofix_reply(
            {"outcome": "fixed", "explanation": "Clamped y.", "validation": "pytest passed."},
            head="abc1234567", fixed_in="abc1234567")
        self.assertIn("**Bring PR to ready — fixed**", reply)
        self.assertIn("Fixed in commit abc1234.", reply)
        self.assertEqual(bot.read_marker(bot.AUTOFIX_REPLY, reply), {"head": "abc1234567", "outcome": "fixed"})
        result = bot.render_autofix_result(
            {"head": "h", "round": 2, "ci_open": ["Studio frontend"], "conflict_files": ["app.py"]},
            blocking="stuck",
            auto_merge_disabled=True,
        )
        self.assertIn("Blocking condition: stuck", result)
        self.assertIn("Auto-merge was turned off", result)
        self.assertIn("Round 2 of 3", result)
        self.assertIn("Open CI failures: `Studio frontend`.", result)
        self.assertIn("Open merge conflicts: `app.py`.", result)
        self.assertEqual(
            bot.read_marker(bot.AUTOFIX_STATE, result)["ci_open"],
            ["Studio frontend"],
        )

    def test_fixed_needs_claudes_own_commit_on_the_branch(self):
        self.assertEqual(bot.classify_push("a", "fix", "fix", True), (True, None))
        self.assertEqual(bot.classify_push("a", "fix", "later", True), (True, None))
        pushed, blocking = bot.classify_push("a", "fix", "maintainer", False)
        self.assertFalse(pushed)
        self.assertIn("not on the pull request's branch", blocking)
        self.assertEqual(bot.classify_push("a", "a", "other", False), (False, "The branch changed while auto-fix ran."))
        self.assertEqual(bot.classify_push("a", "a", "a", True), (False, None))

    def test_protected_paths(self):
        self.assertEqual(
            bot.protected_changes([".github/workflows/ci.yml", "anteroom/VERSION", "anteroom/VERSIONS.md", "CHANGELOG.md", "docs/x.md"]),
            [".github/workflows/ci.yml", "anteroom/VERSION", "CHANGELOG.md"],
        )

    def test_main_merge_changes_are_not_counted_as_protected_edits(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

        def write(path, text):
            (Path(root) / path).parent.mkdir(parents=True, exist_ok=True)
            (Path(root) / path).write_text(text)

        with tempfile.TemporaryDirectory() as root:
            git("init", "-q", "-b", "main")
            git("config", "user.name", "t")
            git("config", "user.email", "t@example.com")
            for path, text in {"app.py": "x = 1\n", "CHANGELOG.md": "# 1\n", "anteroom/VERSION": "1\n",
                               ".github/ci.yml": "a\n"}.items():
                write(path, text)
            git("add", "-A")
            git("commit", "-qm", "base")
            git("checkout", "-qb", "pr")
            write("app.py", "x = 2\n")
            write(".github/ci.yml", "pr\n")
            git("commit", "-qam", "pr")
            git("checkout", "-q", "main")
            write("app.py", "x = 3\n")
            write("CHANGELOG.md", "# 2\n")
            write("anteroom/VERSION", "2\n")
            git("commit", "-qam", "release")
            main_sha = git("rev-parse", "HEAD")
            git("checkout", "-q", "pr")
            head_before = git("rev-parse", "HEAD")
            subprocess.run(["git", "merge", "-q", "main"], cwd=root, capture_output=True, check=False)
            write("app.py", "x = 4\n")
            git("commit", "-qam", "merge main")
            merged = git("rev-parse", "HEAD")
            old = os.getcwd()
            os.chdir(root)
            try:
                self.assertEqual(bot.protected_changes(bot.repair_changed_paths(head_before, merged, main_sha)), [])
                self.assertEqual(bot.repair_changed_paths(head_before, merged, main_sha), ["app.py"])
                # Without the merge, the same diff would wrongly flag main's release files.
                self.assertEqual(
                    bot.protected_changes(bot.repair_changed_paths(head_before, merged, None)),
                    ["CHANGELOG.md", "anteroom/VERSION"],
                )
                write("CHANGELOG.md", "# edited\n")
                git("commit", "-qam", "touch changelog")
                edited = git("rev-parse", "HEAD")
                self.assertEqual(
                    bot.protected_changes(bot.repair_changed_paths(head_before, edited, main_sha)),
                    ["CHANGELOG.md"],
                )
            finally:
                os.chdir(old)

    def _conflict_fixture(self, root, pr_files, main_files):
        """Bare origin with main and a PR branch editing the same files; returns the clone."""
        def git(cwd, *args):
            return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

        origin, work = os.path.join(root, "origin.git"), os.path.join(root, "work")
        git(root, "init", "-q", "--bare", "-b", "main", origin)
        git(root, "clone", "-q", origin, work)
        git(work, "config", "user.name", "t")
        git(work, "config", "user.email", "t@example.com")
        base = {path: "base\n" for path in {**pr_files, **main_files}}
        for files, message in ((base, "base"), (main_files, "main")):
            for path, text in files.items():
                (Path(work) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(work) / path).write_text(text)
            git(work, "add", "-A")
            git(work, "commit", "-qm", message)
            if message == "base":
                git(work, "branch", "pr")
        git(work, "push", "-q", "origin", "main")
        git(work, "checkout", "-q", "pr")
        for path, text in pr_files.items():
            (Path(work) / path).write_text(text)
        git(work, "commit", "-qam", "pr")
        return work, (lambda *a: git(work, *a))

    def _prepare(self, work, out, states=()):
        outputs, posts = {}, []
        old = os.getcwd()
        os.chdir(work)
        try:
            with (
                patch.object(bot, "review_threads", return_value=[]),
                patch.object(bot, "open_autofix_findings", return_value=[]),
                patch.object(bot, "bot_issue_comments", return_value=[({}, s) for s in states]),
                patch.object(bot, "pull_mergeable_state", return_value="dirty"),
                patch.object(bot, "collect_ci_failures", return_value=([], "")),
                patch.object(bot, "api", side_effect=lambda *a, **k: posts.append(a)),
                patch.object(bot, "repo", return_value="o/r"),
                patch.object(bot, "set_output", side_effect=outputs.update),
            ):
                bot.prepare_autofix(SimpleNamespace(pr=7, out=out))
        finally:
            os.chdir(old)
        return outputs, posts

    def test_prepare_starts_the_main_merge_for_product_conflicts(self):
        with tempfile.TemporaryDirectory() as root:
            work, git = self._conflict_fixture(
                root,
                {"app.py": "pr\n", "anteroom/uv.lock": "pr\n"},
                {"app.py": "main\n", "anteroom/uv.lock": "main\n"},
            )
            out = os.path.join(root, "ctx")
            outputs, posts = self._prepare(work, out)
            self.assertEqual(outputs["skip"], "false")
            self.assertEqual(outputs["lockfile_conflict"], "true")
            self.assertEqual(posts, [])
            ctx = json.loads(Path(out, "context.json").read_text())
            self.assertEqual(sorted(ctx["conflict_files"]), ["anteroom/uv.lock", "app.py"])
            self.assertEqual(ctx["main_sha"], git("rev-parse", "origin/main"))
            self.assertFalse(ctx["dependencies_installed"])
            # The merge is left in progress for Claude to finish.
            self.assertTrue(Path(work, ".git", "MERGE_HEAD").exists())

    def test_prepare_leaves_protected_only_conflicts_and_notes_them_once(self):
        with tempfile.TemporaryDirectory() as root:
            work, git = self._conflict_fixture(root, {"CHANGELOG.md": "pr\n"}, {"CHANGELOG.md": "main\n"})
            head = git("rev-parse", "HEAD")
            outputs, posts = self._prepare(work, os.path.join(root, "ctx"))
            self.assertEqual(outputs, {"skip": "true"})
            self.assertEqual(len(posts), 1)
            self.assertFalse(Path(work, ".git", "MERGE_HEAD").exists())
            self.assertEqual(git("rev-parse", "HEAD"), head)
            noted = [{"head": head, "reason": "protected_conflicts", "conflict_files": ["CHANGELOG.md"]}]
            outputs, posts = self._prepare(work, os.path.join(root, "ctx2"), states=noted)
            self.assertEqual(outputs, {"skip": "true"})
            self.assertEqual(posts, [])

    def test_prepare_leaves_mixed_protected_and_product_conflicts_and_notes_them_once(self):
        # Claude cannot edit the protected file, so running it could never finish the merge.
        with tempfile.TemporaryDirectory() as root:
            work, git = self._conflict_fixture(
                root,
                {"app.py": "pr\n", "CHANGELOG.md": "pr\n"},
                {"app.py": "main\n", "CHANGELOG.md": "main\n"},
            )
            head = git("rev-parse", "HEAD")
            outputs, posts = self._prepare(work, os.path.join(root, "ctx"))
            self.assertEqual(outputs, {"skip": "true"})
            self.assertEqual(len(posts), 1)
            self.assertIn("Merge conflicts include protected paths", posts[0][2]["body"])
            self.assertFalse(Path(work, ".git", "MERGE_HEAD").exists())
            self.assertEqual(git("rev-parse", "HEAD"), head)
            noted = [{"head": head, "reason": "protected_conflicts", "conflict_files": ["CHANGELOG.md", "app.py"]}]
            outputs, posts = self._prepare(work, os.path.join(root, "ctx2"), states=noted)
            self.assertEqual(outputs, {"skip": "true"})
            self.assertEqual(posts, [])


if __name__ == "__main__":
    unittest.main()
