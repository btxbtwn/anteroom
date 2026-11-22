"""Tests for review_bot.py's formatting and decision logic. Run:
python3 .github/review-bot/test_review_bot.py"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

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

    def test_split_findings_moves_lines_outside_the_diff(self):
        inline, outside = bot.split_findings([FINDING, FINDING | {"line": 40}], DIFF)
        self.assertEqual([f["line"] for f in inline], [3])
        self.assertEqual([f["line"] for f in outside], [40])

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

    def test_review_body_counts_inline_and_lists_files(self):
        ctx = {"head": "h" * 40, "merge_base": "m" * 40, "since": "s" * 40, "files": ["app.py"]}
        body = bot.render_review_body([FINDING], [FINDING | {"line": 40}], ctx)
        self.assertTrue(body.startswith("**Actionable comments posted: 1**"))
        self.assertIn("Outside diff range comments (1)", body)
        self.assertIn(f"between {'s' * 40} and {'h' * 40}", body)

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

    def test_changed_paths_keeps_whitespace_and_both_rename_endpoints(self):
        with TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.check_output(["git", "-C", tmp, *args])
            git("init", "-q")
            (Path(tmp) / "CHANGELOG.md").write_text("notes")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "base")
            git("mv", "CHANGELOG.md", "review notes.md")
            with patch.dict("os.environ", GIT_DIR=str(Path(tmp) / ".git"), GIT_WORK_TREE=tmp):
                git("-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "move")
                paths = bot.changed_paths("HEAD^", "HEAD")
            self.assertEqual(paths, ["CHANGELOG.md", "review notes.md"])
            self.assertEqual(bot.protected_changes(paths), ["CHANGELOG.md"])

    def test_body_findings_lifecycle(self):
        ctx = {"head": "abc", "merge_base": "base", "files": ["app.py"]}
        body = bot.render_review_body([], [FINDING, FINDING | {"line": 42}], ctx)
        review = {"user": {"login": "github-actions[bot]"}, "id": 12, "body": body, "html_url": "review"}
        with patch.object(bot, "repo", return_value="o/r"), patch.object(bot, "api_list", return_value=[review]), \
             patch.object(bot, "bot_issue_comments") as history:
            history.return_value = []
            found = bot.body_findings(1, "abc", for_autofix=True)
            self.assertEqual([f["comment_id"] for f in found], ["review:12:0", "review:12:1"])
            history.return_value = [({"body": "done"}, {"id": found[0]["comment_id"], "outcome": "fixed"}),
                                    ({"body": "closed"}, {"id": found[1]["comment_id"], "addressed": True})]
            self.assertEqual(bot.body_findings(1, "abc", for_autofix=True), [])
            self.assertEqual(len(bot.body_findings(1, "abc", for_autofix=False)), 1)

    def test_interrupted_round_resumes_without_reposting_answered_reply(self):
        journal = {"ctx": {"pr": 1, "head": "base", "round": 3}, "target": "tip",
                   "auto_merge_disabled": False, "findings": [{"comment_id": 7}],
                   "results": [{"comment_id": 7, "outcome": "fixed", "explanation": "ok", "validation": "tested"}]}
        reply = bot.marker(bot.AUTOFIX_REPLY, {"head": "tip", "outcome": "fixed"})
        with patch.object(bot, "is_ancestor", return_value=True), patch.object(bot, "repo", return_value="o/r"), \
             patch.object(bot, "review_threads", return_value=[thread(bot.CODEX_LOGIN, "x", [reply])]), \
             patch.object(bot, "bot_issue_comments", return_value=[]), patch.object(bot, "api") as api:
            bot.finish_journal({"id": 44}, journal, "newhead")
            self.assertEqual(api.call_count, 1)
            path, method, data = api.call_args.args
            self.assertEqual((path, method), ("repos/o/r/issues/comments/44", "PATCH"))
            state = bot.read_marker(bot.AUTOFIX_STATE, data["body"])
            self.assertTrue(state["pushed"])
            self.assertEqual(state["round"], 3)

    def test_protected_paths(self):
        self.assertEqual(
            bot.protected_changes([".github/workflows/ci.yml", "VERSION", "anteroom/VERSION", "VERSIONS.md", "CHANGELOG.md", "docs/x.md"]),
            [".github/workflows/ci.yml", "VERSION", "anteroom/VERSION", "CHANGELOG.md"],
        )


if __name__ == "__main__":
    unittest.main()
