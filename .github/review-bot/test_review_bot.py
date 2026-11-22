"""Tests for review_bot.py's formatting and decision logic. Run:
python3 .github/review-bot/test_review_bot.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
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
        self.assertIn("--diff-filter=d", diff_paths.call_args.args[0])


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
        pushed = [{"head": f"h{i}", "pushed": True} for i in range(bot.MAX_ROUNDS)]
        self.assertEqual(bot.autofix_decision(pushed, [1], "h9"), ("skip", "max_rounds"))
        stopped = pushed + [{"head": "h9", "reason": "max_rounds"}]
        self.assertEqual(bot.autofix_decision(stopped, [1], "h9"), ("skip", None))
        tried = [{"head": "h", "pushed": False, "open": [1, 2]}]
        self.assertEqual(bot.autofix_decision(tried, [1], "h"), ("skip", "same_blockers"))
        self.assertEqual(bot.autofix_decision(tried, [1, 3], "h"), ("run", None))
        self.assertEqual(bot.autofix_decision(tried, [1], "h2"), ("run", None))

    def test_reply_and_result_text(self):
        reply = bot.render_autofix_reply(
            {"outcome": "fixed", "explanation": "Clamped y.", "validation": "pytest passed."},
            head="abc1234567", fixed_in="abc1234567")
        self.assertIn("**Bring PR to ready — fixed**", reply)
        self.assertIn("Fixed in commit abc1234.", reply)
        self.assertEqual(bot.read_marker(bot.AUTOFIX_REPLY, reply), {"head": "abc1234567", "outcome": "fixed"})
        result = bot.render_autofix_result({"head": "h", "round": 2}, blocking="stuck", auto_merge_disabled=True)
        self.assertIn("Blocking condition: stuck", result)
        self.assertIn("Auto-merge was turned off", result)
        self.assertIn("Round 2 of 3", result)

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


if __name__ == "__main__":
    unittest.main()
