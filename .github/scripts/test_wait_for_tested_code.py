"""Offline gate recovery tests for wait-for-tested-code.py (no network, app, or DB)."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import recovery_evidence as recovery
import verification_evidence as verification


def _load_wait_gate():
    spec = importlib.util.spec_from_file_location("wait_for_tested_code", SCRIPTS_DIR / "wait-for-tested-code.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(name: str, payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, json.dumps(payload))
    return buf.getvalue()


class FakeHealthResponse:
    def __init__(self, body: bytes, status: int = 200, url: str = "https://myanteroom.com/api/health"):
        self._body = body
        self.status = status
        self._url = url

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class WaitForTestedCodeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.gate = _load_wait_gate()
        self.repo = "example/project"
        self.prod = "1" * 40
        self.before_hist = "2" * 40
        self.hist = verification.HISTORICAL
        self.corr = verification.CORRECTED
        self.target = "3" * 40
        self.post_merge = "4" * 40
        self.head = "5" * 40
        self.off_branch = "6" * 40
        self.tree = "7" * 40
        self.wf_blob = "8" * 40
        self.ci_blob = "9" * 40

        self.span_to_target = [self.before_hist, self.hist, self.corr, self.target]
        self.span_to_head = [*self.span_to_target, self.post_merge, self.head]
        self.main_newest_first = list(reversed([self.prod, *self.span_to_head]))

        self.git_values = {
            ("rev-parse", self.head): self.head,
            ("rev-parse", self.target): self.target,
            ("rev-parse", self.corr): self.corr,
            ("rev-parse", self.off_branch): self.off_branch,
            ("rev-parse", self.post_merge): self.post_merge,
            ("rev-parse", "HEAD"): self.head,
            ("rev-list", "--first-parent", "origin/main"): "\n".join(self.main_newest_first),
            ("rev-list", "--first-parent", "--reverse", f"{self.prod}..{self.head}"): "\n".join(self.span_to_head),
            ("rev-list", "--first-parent", "--reverse", f"{self.prod}..{self.target}"): "\n".join(self.span_to_target),
            ("rev-list", "--first-parent", "--reverse", f"{self.prod}..{self.corr}"): "\n".join(
                [self.before_hist, self.hist, self.corr]
            ),
            ("rev-parse", self.target + "^{tree}"): self.tree,
            ("rev-parse", self.target + ":" + verification.WORKFLOW): self.wf_blob,
            ("rev-parse", self.target + ":.github/workflows/ci.yml"): self.ci_blob,
            ("diff", "--name-only", "--no-renames", "-z", f"{self.prod}..{self.target}"): "",
            ("show", f"{self.prod}:vendoo-studio/VERSION"): "0.1.1011",
        }

        self.run = {
            "id": 42,
            "workflow_id": 20,
            "head_sha": self.target,
            "head_branch": "main",
            "repository": {"id": 10, "full_name": self.repo},
            "head_repository": {"id": 10},
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "path": verification.WORKFLOW,
            "status": "completed",
            "conclusion": "success",
        }
        self.source = {
            "schema": 1,
            "repository": self.repo,
            "target": self.target,
            "tree": self.tree,
            "production_base": self.prod,
            "production_base_is_live_verified": False,
            "historical_failure": self.hist,
            "correcting_merge": self.corr,
            "span": self.span_to_target,
            "native_changes": [],
            "migration_changes": [],
            "workflow": verification.WORKFLOW,
            "workflow_blob": self.wf_blob,
            "ci_blob": self.ci_blob,
            "run_id": "42",
            "attempt": "1",
            "event": "workflow_dispatch",
            "deployment_authorized": False,
        }
        self.jobs = [
            {"id": i, "run_id": 42, "name": name, "status": "completed", "conclusion": "success"}
            for i, name in enumerate(
                ["prepare", *["checks / " + n for n in sorted(verification.CHECKS)], "evidence"], 1
            )
        ]
        ids = [verification.digest(verification.REGRESSION), verification.digest("other-test")]
        self.inventories = [
            {
                **{k: self.source[k] for k in ("target", "tree", "run_id", "attempt")},
                "database": db,
                "group": group,
                "expected": ids,
                "selected": [ids[group - 1]],
                "passed": [ids[group - 1]],
                "skipped": [],
            }
            for db in ("sqlite", "postgres")
            for group in (1, 2)
        ]
        self.receipt = {
            **self.source,
            "jobs": verification.validate_jobs(self.jobs),
            "backend_inventory": verification.validate_inventory(self.inventories, self.source),
            "scope": "all Linux CI components; native validation remains separate",
            "native_validation": "not performed; separate native impact assessment and pinned receipts required",
        }
        self.artifacts = []
        self.downloads = {}
        self._add_artifact(100, "verification-receipt-42-1", "verification-receipt.json", self.receipt)
        self._add_artifact(101, "verification-source-42-1", "verification-source.json", self.source)
        for idx, inv in enumerate(self.inventories, 102):
            self._add_artifact(
                idx,
                f"verification-inventory-{inv['database']}-{inv['group']}-42-1",
                f"inventory-{inv['database']}-{inv['group']}.json",
                inv,
            )
        self.pin = recovery.EvidencePin(
            repository=self.repo,
            repository_id=10,
            workflow_id=20,
            target=self.target,
            tree=self.tree,
            production_base=self.prod,
            workflow_blob=self.wf_blob,
            ci_blob=self.ci_blob,
            run_id=42,
            attempt=1,
            receipt_artifact_id=100,
            receipt_digest=self.artifacts[0]["digest"],
        )

    def _add_artifact(self, ident: int, name: str, filename: str, payload: dict) -> None:
        raw = _archive(filename, payload)
        self.downloads[ident] = raw
        self.artifacts.append(
            {
                "id": ident,
                "name": name,
                "expired": False,
                "size_in_bytes": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "workflow_run": {
                    "id": 42,
                    "repository_id": 10,
                    "head_repository_id": 10,
                    "head_sha": self.target,
                    "head_branch": "main",
                },
            }
        )

    def fake_git(self, *args: str) -> str:
        if args[0] == "merge-base":
            return ""
        return self.git_values[args]

    def fake_fetch(self, path: str):
        if path in ("actions/runs/42", "actions/runs/42/attempts/1"):
            return copy.deepcopy(self.run)
        if path.startswith("actions/runs/42/attempts/1/jobs?"):
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if path.startswith("actions/runs/42/artifacts?"):
            return {"total_count": len(self.artifacts), "artifacts": copy.deepcopy(self.artifacts)}
        raise AssertionError(f"Unexpected API path: {path}")

    def test_committed_pin_matches_exact_documented_receipt(self):
        pin = self.gate.HISTORICAL_RECOVERY_CHECKPOINT
        self.assertEqual(pin.repository, "btxbtwn/anteroom")
        self.assertEqual(pin.repository_id, 1183824317)
        self.assertEqual(pin.workflow_id, 422931456)
        self.assertEqual(pin.target, "b28ba4b361071373731b93bc49c12b4bf78326e9")
        self.assertEqual(pin.production_base, "b73c534707b9ef9bd70e4dc589b9c4b10863a852")
        self.assertEqual(pin.run_id, 44662061597)
        self.assertEqual(pin.attempt, 1)
        self.assertEqual(pin.receipt_artifact_id, 14596571781)
        self.assertEqual(
            pin.receipt_digest,
            "sha256:4d0f38c8b8125c78810c0eecfb9301e74bd73a8e3f2df148cb38879c328ed791",
        )

    def test_valid_checkpoint_passes_when_base_live_version_and_receipt_match(self):
        report = self.gate.verify_historical_checkpoint(
            self.repo,
            self.head,
            self.prod,
            self.hist,
            pin=self.pin,
            health_url="https://myanteroom.com/api/health",
            fetch=self.fake_fetch,
            download=self.downloads.__getitem__,
            git_fn=self.fake_git,
            live_version_fn=lambda url: "0.1.1011",
        )
        self.assertEqual(report["target"], self.target)
        self.assertEqual(report["production_base"], self.prod)
        self.assertEqual(report["live_base_version"], "0.1.1011")

    def test_wrong_sha_and_ancestry_mutations_fail_closed(self):
        # Cannot apply receipt to a different target SHA (e.g. post_merge or head).
        bad_pin = replace(self.pin, target=self.post_merge)
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.head, self.prod, self.hist, pin=bad_pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        # Cannot deploy a head earlier than the verified checkpoint.
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.corr, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        # Cannot deploy an off-main commit.
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.off_branch, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        # Cannot supersede any other failed commit (before or after checkpoint).
        for other_sha in (self.before_hist, self.corr, self.post_merge):
            with self.subTest(other_sha=other_sha), self.assertRaises(ValueError):
                self.gate.verify_historical_checkpoint(
                    self.repo, self.head, self.prod, other_sha, pin=self.pin,
                    health_url="https://myanteroom.com/api/health",
                    fetch=self.fake_fetch, download=self.downloads.__getitem__,
                    git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
                )
        # Wrong repository fails closed.
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                "other/project", self.head, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        # Missing recorded production commit fails closed.
        eligible, _ = self.gate.checkpoint_eligible(
            self.repo, self.head, None, self.hist, pin=self.pin, git_fn=self.fake_git
        )
        self.assertFalse(eligible)

    def test_stale_evidence_and_live_version_drift_fail_closed(self):
        # Expired receipt artifact fails closed.
        self.artifacts[0]["expired"] = True
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.head, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        self.artifacts[0]["expired"] = False

        # Newer attempt on verification run fails closed.
        self.run["run_attempt"] = 2
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.head, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )
        self.run["run_attempt"] = 1

        # Live production version drift (e.g. partial prior rollout) fails closed.
        with self.assertRaisesRegex(ValueError, "Live production version"):
            self.gate.verify_historical_checkpoint(
                self.repo, self.head, self.prod, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1048",
            )

        # Recorded production already advanced past pinned base fails closed.
        with self.assertRaises(ValueError):
            self.gate.verify_historical_checkpoint(
                self.repo, self.head, self.target, self.hist, pin=self.pin,
                health_url="https://myanteroom.com/api/health",
                fetch=self.fake_fetch, download=self.downloads.__getitem__,
                git_fn=self.fake_git, live_version_fn=lambda _: "0.1.1011",
            )

    def test_live_health_version_validation(self):
        for bad_url in ("", "http://myanteroom.com/api/health", "file:///etc/passwd"):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                self.gate.live_health_version(bad_url)

        ok_body = json.dumps({"status": "ok", "service": "anteroom-hosted", "version": "0.1.1011"}).encode()
        with patch.object(self.gate.urllib.request, "urlopen", return_value=FakeHealthResponse(ok_body)):
            self.assertEqual(self.gate.live_health_version("https://myanteroom.com/api/health"), "0.1.1011")

        # Network/HTTP errors fail closed.
        for exc in (
            self.gate.urllib.error.HTTPError("https://myanteroom.com/api/health", 503, "Unavailable", {}, None),
            self.gate.http.client.IncompleteRead(b"partial"),
        ):
            with (
                self.subTest(exc=type(exc).__name__),
                patch.object(self.gate.urllib.request, "urlopen", side_effect=exc),
                self.assertRaises(ValueError),
            ):
                self.gate.live_health_version("https://myanteroom.com/api/health")

        # Redirect to another host or http fails closed.
        for bad_dest in ("http://myanteroom.com/api/health", "https://evil.example/api/health"):
            with (
                self.subTest(bad_dest=bad_dest),
                patch.object(
                    self.gate.urllib.request, "urlopen",
                    return_value=FakeHealthResponse(ok_body, url=bad_dest),
                ),
                self.assertRaises(ValueError),
            ):
                self.gate.live_health_version("https://myanteroom.com/api/health")

        # Degraded status, wrong service, missing version, non-JSON, or oversized payload fails closed.
        bad_bodies = [
            b"not-json",
            json.dumps({"status": "degraded", "service": "anteroom-hosted", "version": "0.1.1011"}).encode(),
            json.dumps({"status": "ok", "service": "other", "version": "0.1.1011"}).encode(),
            json.dumps({"status": "ok", "service": "anteroom-hosted", "version": ""}).encode(),
            b" " * (self.gate.MAX_HEALTH_BYTES + 1),
        ]
        for body in bad_bodies:
            with (
                patch.object(self.gate.urllib.request, "urlopen", return_value=FakeHealthResponse(body)),
                self.assertRaises(ValueError),
            ):
                self.gate.live_health_version("https://myanteroom.com/api/health")

    def test_main_waits_for_post_checkpoint_commits_before_verifying_and_rejects_post_failure(self):
        verify_calls = []
        poll_count = {"post": 0}

        def fake_ci_state(repo, sha):
            if sha == self.hist:
                return ("completed", "failure")
            if sha == self.post_merge:
                poll_count["post"] += 1
                return ("in_progress", "") if poll_count["post"] == 1 else ("completed", "success")
            return ("completed", "success")

        def fake_verify(repo, head, prod, failed_sha, **kwargs):
            # Must only run after post_merge finished succeeding.
            self.assertEqual(poll_count["post"], 2)
            verify_calls.append((repo, head, prod, failed_sha))
            return {
                "target": self.target,
                "production_base": self.prod,
                "run_id": 42,
                "attempt": 1,
                "receipt_artifact_id": 100,
                "live_base_version": "0.1.1011",
            }

        with (
            patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "_read_git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(
                self.gate, "commits_to_check",
                return_value=([self.before_hist, self.hist, self.corr, self.post_merge], "span"),
            ),
            patch.object(self.gate, "ci_state", side_effect=fake_ci_state),
            patch.object(self.gate, "verify_historical_checkpoint", side_effect=fake_verify),
            patch.object(self.gate.time, "sleep", return_value=None),
            patch.object(
                sys, "argv",
                ["gate", "--repo", self.repo, "--head", self.head, "--timeout", "60", "--interval", "0",
                 "--health-url", "https://myanteroom.com/api/health"],
            ),
        ):
            self.assertEqual(self.gate.main(), 0)
        self.assertEqual(verify_calls, [(self.repo, self.head, self.prod, self.hist)])

        # A failure on a commit AFTER the checkpoint must fail closed without invoking recovery verification.
        verify_calls.clear()
        with (
            patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "_read_git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(
                self.gate, "commits_to_check",
                return_value=([self.before_hist, self.hist, self.corr, self.post_merge], "span"),
            ),
            patch.object(
                self.gate, "ci_state",
                side_effect=lambda r, s: ("completed", "failure") if s in (self.hist, self.post_merge) else ("completed", "success"),
            ),
            patch.object(self.gate, "verify_historical_checkpoint", side_effect=fake_verify),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.head, "--timeout", "0"]),
        ):
            self.assertEqual(self.gate.main(), 1)
        self.assertEqual(verify_calls, [])

    def test_concurrent_or_repeated_attempt_cannot_reuse_checkpoint_after_production_advances(self):
        # Scenario 1: production advances while waiting for another commit's CI -> fails closed.
        prod_reads = iter([self.prod, self.head])
        with (
            patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "_read_git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", side_effect=lambda _: next(prod_reads)),
            patch.object(
                self.gate, "commits_to_check",
                return_value=([self.hist, self.corr, self.post_merge], "span"),
            ),
            patch.object(
                self.gate, "ci_state",
                side_effect=lambda r, s: ("completed", "failure") if s == self.hist else ("completed", "success"),
            ),
            patch.object(
                self.gate, "verify_historical_checkpoint",
                side_effect=AssertionError("Must fail before verifying when recorded production moved"),
            ),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.head, "--timeout", "0"]),
        ):
            self.assertEqual(self.gate.main(), 1)

        # Scenario 2: after Attempt 1 deploys and advances production_commit() to head,
        # Attempt 2 runs the ordinary deployment path with zero checkpoint calls.
        new_commit = "a" * 40
        with (
            patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
            patch.object(self.gate, "git", side_effect=lambda *a: new_commit if a == ("rev-parse", new_commit) else self.fake_git(*a)),
            patch.object(self.gate, "production_commit", return_value=self.head),
            patch.object(self.gate, "commits_to_check", return_value=([new_commit], "ordinary span")),
            patch.object(self.gate, "ci_state", return_value=("completed", "success")),
            patch.object(
                self.gate, "verify_historical_checkpoint",
                side_effect=AssertionError("Ordinary deploy must never invoke recovery checkpoint"),
            ),
            patch.object(
                self.gate, "live_health_version",
                side_effect=AssertionError("Ordinary deploy must never probe live health"),
            ),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", new_commit, "--timeout", "0"]),
        ):
            self.assertEqual(self.gate.main(), 0)

        # Scenario 3: cancelled conclusion on historical SHA is rejected immediately.
        with (
            patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "_read_git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(self.gate, "commits_to_check", return_value=([self.hist, self.corr], "span")),
            patch.object(
                self.gate, "ci_state",
                side_effect=lambda r, s: ("completed", "cancelled") if s == self.hist else ("completed", "success"),
            ),
            patch.object(
                self.gate, "verify_historical_checkpoint",
                side_effect=AssertionError("Cancelled historical CI must not defer to recovery"),
            ),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.head, "--timeout", "0"]),
        ):
            self.assertEqual(self.gate.main(), 1)

    def _base_machines(self, *, label=None, digest=None, include_worker=True, worker_state="started", worker_label=None, worker_digest=None):
        base_label = label or f"v0.1.1011-{self.prod[:8]}"
        base_digest = digest or self.gate.HISTORICAL_BASE_IMAGE_DIGEST
        machines = [
            {
                "id": "web-1",
                "state": "started",
                "image_ref": {"tag": base_label, "digest": base_digest},
                "config": {"metadata": {"fly_process_group": "app"}},
            }
        ]
        if include_worker:
            machines.append(
                {
                    "id": "worker-1",
                    "state": worker_state,
                    "image_ref": {"tag": worker_label or base_label, "digest": worker_digest or base_digest},
                    "config": {"metadata": {"fly_process_group": "worker"}},
                }
            )
        return machines

    def test_migration_success_then_rollout_failure_blocks_checkpoint_reuse(self):
        """Simulate Attempt 1 migrating DB then failing before moving web or recording Deployed commit."""
        live_state = {
            "recorded_production": self.prod,
            "health_version": "0.1.1011",
            "machines": self._base_machines(),
            "db_revision": "d0e4f3a92b68",
        }
        effects = []

        orig_verify = self.gate.verify_historical_checkpoint

        def run_recovery_attempt():
            # Step 1: Wait gate verifies receipt and sets recovery_checkpoint=true.
            with tempfile.TemporaryDirectory() as tmp:
                gh_out = Path(tmp) / "github_output"
                with (
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(gh_out)}),
                    patch.object(self.gate, "HISTORICAL_RECOVERY_CHECKPOINT", self.pin),
                    patch.object(self.gate, "git", side_effect=self.fake_git),
                    patch.object(self.gate, "_read_git", side_effect=self.fake_git),
                    patch.object(self.gate, "production_commit", side_effect=lambda _: live_state["recorded_production"]),
                    patch.object(
                        self.gate, "commits_to_check",
                        return_value=([self.before_hist, self.hist, self.corr, self.post_merge], "span"),
                    ),
                    patch.object(
                        self.gate, "ci_state",
                        side_effect=lambda r, s: ("completed", "failure") if s == self.hist else ("completed", "success"),
                    ),
                    patch.object(
                        self.gate, "verify_historical_checkpoint",
                        side_effect=lambda *a, **kw: orig_verify(
                            self.repo, self.head, live_state["recorded_production"], self.hist,
                            pin=self.pin, health_url="https://myanteroom.com/api/health",
                            fetch=self.fake_fetch, download=self.downloads.__getitem__,
                            git_fn=self.fake_git, live_version_fn=lambda _: live_state["health_version"],
                        ),
                    ),
                    patch.object(
                        sys, "argv",
                        ["gate", "--repo", self.repo, "--head", self.head, "--timeout", "0",
                         "--health-url", "https://myanteroom.com/api/health"],
                    ),
                ):
                    wait_rc = self.gate.main()
                if wait_rc != 0:
                    return wait_rc, ""
                out_text = gh_out.read_text(encoding="utf-8")
                self.assertIn("recovery_checkpoint=true\n", out_text)

            # Step 2: Pre-effect live reconciliation checks recorded SHA, health, Fly web+worker, and live DB revision.
            try:
                self.gate.reconcile_live_production_base(
                    self.repo,
                    fly_app=self.gate.HISTORICAL_BASE_FLY_APP,
                    health_url="https://myanteroom.com/api/health",
                    pin=self.pin,
                    production_fn=lambda _: live_state["recorded_production"],
                    git_fn=self.fake_git,
                    live_version_fn=lambda _: live_state["health_version"],
                    fly_machines_fn=lambda _: copy.deepcopy(live_state["machines"]),
                    db_revision_fn=lambda _: live_state["db_revision"],
                )
            except ValueError as err:
                return 1, str(err)
            effects.append("deploy-effects-started")
            return 0, ""

        # Attempt 1: untouched proven base passes both gate and pre-effect reconciliation.
        rc1, err1 = run_recovery_attempt()
        self.assertEqual((rc1, err1), (0, ""))
        self.assertEqual(effects, ["deploy-effects-started"])

        # Simulate migration succeeding (DB moves to a70c21f496e8) and Fly rollout failing before moving
        # web or recording Deployed commit: web health STILL says 0.1.1011 and recorded SHA is STILL base.
        live_state["db_revision"] = "a70c21f496e8"
        effects.clear()
        rc2, err2 = run_recovery_attempt()
        self.assertEqual(rc2, 1)
        self.assertIn("a70c21f496e8", err2)
        self.assertIn("d0e4f3a92b68", err2)
        self.assertEqual(effects, [], "Attempt 2 must fail closed before any build/migration/rollout effect")

        # Worker drifted to new image while web still reports 0.1.1011 and DB was reset -> still fails closed.
        live_state["db_revision"] = "d0e4f3a92b68"
        live_state["machines"] = self._base_machines(worker_label="v0.1.1048-deadbeef")
        rc3, err3 = run_recovery_attempt()
        self.assertEqual(rc3, 1)
        self.assertIn("worker Machine worker-1", err3)
        self.assertEqual(effects, [])

        # Web-only Fly Machine list (worker missing) -> fails closed.
        live_state["machines"] = self._base_machines(include_worker=False)
        rc4, err4 = run_recovery_attempt()
        self.assertEqual(rc4, 1)
        self.assertIn("required process group 'worker'", err4)
        self.assertEqual(effects, [])

        # Worker stopped or running same tag with different OCI digest -> fails closed.
        live_state["machines"] = self._base_machines(worker_state="stopped")
        rc5, err5 = run_recovery_attempt()
        self.assertEqual(rc5, 1)
        self.assertIn("stopped", err5)

        live_state["machines"] = self._base_machines(worker_digest="sha256:" + "0" * 64)
        rc6, err6 = run_recovery_attempt()
        self.assertEqual(rc6, 1)
        self.assertIn("digest", err6)

    def test_reconcile_live_base_cli_and_alembic_validation(self):
        # parse_alembic_current accepts exact single revision with or without (head).
        self.assertEqual(self.gate.parse_alembic_current("d0e4f3a92b68\n"), "d0e4f3a92b68")
        self.assertEqual(self.gate.parse_alembic_current("a70c21f496e8 (head)\n"), "a70c21f496e8")
        for bad in ("", "\n", "d0e4f3a92b68\na70c21f496e8 (head)\n", "not-a-rev\n", "d0e4f3a92b68 (branch)\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.gate.parse_alembic_current(bad)

        # live_alembic_revision validates Postgres URL, verify-full TLS, CA file, and alembic.ini
        # without ever leaking ANTEROOM_DATABASE_URL.
        secret_url = "postgresql://user:super-secret-password@db.example:5432/postgres"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ca_file = tmp_path / "ca.crt"
            ca_file.write_text("dummy-ca\n", encoding="utf-8")
            (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                self.gate.live_alembic_revision(tmp_path, env={"ANTEROOM_DATABASE_URL": "sqlite:///local.db"})
            self.assertNotIn("sqlite:///local.db", str(ctx.exception))

            with self.assertRaises(ValueError):
                self.gate.live_alembic_revision(
                    tmp_path,
                    env={"ANTEROOM_DATABASE_URL": secret_url, "PGSSLMODE": "require", "PGSSLROOTCERT": str(ca_file)},
                )

            with self.assertRaises(ValueError):
                self.gate.live_alembic_revision(
                    tmp_path,
                    env={"ANTEROOM_DATABASE_URL": secret_url, "PGSSLMODE": "verify-full", "PGSSLROOTCERT": str(tmp_path / "missing.crt")},
                )

            good_env = {
                "ANTEROOM_DATABASE_URL": secret_url,
                "PGSSLMODE": "verify-full",
                "PGSSLROOTCERT": str(ca_file),
            }
            # Transient pooler failure retries once and succeeds; stderr is suppressed.
            results = iter([
                subprocess.CompletedProcess(["python"], 1, stdout=""),
                subprocess.CompletedProcess(["python"], 0, stdout="d0e4f3a92b68\n"),
            ])
            with (
                patch.object(self.gate.subprocess, "run", side_effect=lambda *a, **kw: next(results)),
                patch.object(self.gate.time, "sleep", return_value=None),
            ):
                self.assertEqual(self.gate.live_alembic_revision(tmp_path, env=good_env, retries=2, retry_delay=0), "d0e4f3a92b68")

            # Persistent non-zero exit fails closed without leaking the DB URL.
            with (
                patch.object(
                    self.gate.subprocess, "run",
                    return_value=subprocess.CompletedProcess(["python"], 1, stdout=""),
                ),
                patch.object(self.gate.time, "sleep", return_value=None),
                self.assertRaises(ValueError) as err_ctx,
            ):
                self.gate.live_alembic_revision(tmp_path, env=good_env, retries=2, retry_delay=0)
            self.assertNotIn("super-secret-password", str(err_ctx.exception))

        # CLI --reconcile-live-base returns 0 on match and 1 on mismatch.
        with (
            patch.object(
                self.gate, "reconcile_live_production_base",
                return_value={
                    "production_base": self.prod,
                    "live_base_version": "0.1.1011",
                    "fly_app": self.gate.HISTORICAL_BASE_FLY_APP,
                    "fly_label": f"v0.1.1011-{self.prod[:8]}",
                    "fly_groups": {"app": 1, "worker": 1},
                    "fly_digest": self.gate.HISTORICAL_BASE_IMAGE_DIGEST,
                    "db_revision": "d0e4f3a92b68",
                },
            ),
            patch.object(
                sys, "argv",
                ["gate", "--repo", self.repo, "--reconcile-live-base", "--fly-app", self.gate.HISTORICAL_BASE_FLY_APP],
            ),
        ):
            self.assertEqual(self.gate.main(), 0)

        with (
            patch.object(self.gate, "reconcile_live_production_base", side_effect=ValueError("DB drifted")),
            patch.object(
                sys, "argv",
                ["gate", "--repo", self.repo, "--reconcile-live-base", "--fly-app", self.gate.HISTORICAL_BASE_FLY_APP],
            ),
        ):
            self.assertEqual(self.gate.main(), 1)

    def test_ordinary_deploy_writes_recovery_checkpoint_false(self):
        new_commit = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            gh_out = Path(tmp) / "github_output"
            with (
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(gh_out)}),
                patch.object(self.gate, "git", side_effect=lambda *a: new_commit if a == ("rev-parse", new_commit) else self.fake_git(*a)),
                patch.object(self.gate, "production_commit", return_value=self.head),
                patch.object(self.gate, "commits_to_check", return_value=([new_commit], "ordinary span")),
                patch.object(self.gate, "ci_state", return_value=("completed", "success")),
                patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", new_commit, "--timeout", "0"]),
            ):
                self.assertEqual(self.gate.main(), 0)
            self.assertEqual(gh_out.read_text(encoding="utf-8"), "recovery_checkpoint=false\n")

    def test_check_fly_machines_enforces_required_worker_and_app_groups(self):
        checker = self.gate._load_fly_machine_checker()
        both = self._base_machines(label="v0.1.1048-cafebabe", digest="sha256:" + "a" * 64)
        groups, wrong = checker.problems(both, "v0.1.1048-cafebabe", required_groups=("app", "worker"))
        self.assertEqual((groups, wrong), ({"app": 1, "worker": 1}, []))

        web_only = self._base_machines(label="v0.1.1048-cafebabe", include_worker=False)
        _, wrong_web_only = checker.problems(web_only, "v0.1.1048-cafebabe", required_groups=("app", "worker"))
        self.assertTrue(any("required process group 'worker'" in item for item in wrong_web_only))

        worker_only = [m for m in both if m["config"]["metadata"]["fly_process_group"] == "worker"]
        _, wrong_worker_only = checker.problems(worker_only, "v0.1.1048-cafebabe", required_groups=("app", "worker"))
        self.assertTrue(any("required process group 'app'" in item for item in wrong_worker_only))

    def test_deploy_hosted_workflow_wires_pre_effect_reconciliation_and_worker_check(self):
        workflow_text = (SCRIPTS_DIR.parent / "workflows" / "deploy-hosted.yml").read_text(encoding="utf-8")
        wait_pos = workflow_text.index("name: Wait for CI on the code being shipped")
        install_pos = workflow_text.index("name: Install the hosted server")
        reconcile_pos = workflow_text.index("name: Reconcile live production base for recovery")
        build_pos = workflow_text.index("name: Build the image")
        migrate_pos = workflow_text.index("name: Apply pending migrations")
        deploy_pos = workflow_text.index("name: Deploy to Fly.io")
        confirm_pos = workflow_text.index("name: Confirm every Machine runs the new image")
        self.assertLess(wait_pos, install_pos)
        self.assertLess(install_pos, reconcile_pos)
        self.assertLess(reconcile_pos, build_pos)
        self.assertLess(build_pos, migrate_pos)
        self.assertLess(migrate_pos, deploy_pos)
        self.assertLess(deploy_pos, confirm_pos)
        self.assertIn("RECOVERY_CHECKPOINT: ${{ steps.wait.outputs.recovery_checkpoint }}", workflow_text)
        self.assertIn("--reconcile-live-base", workflow_text)
        self.assertIn('if [ "$RECOVERY_CHECKPOINT" != "true" ]; then', workflow_text)
        self.assertIn("--require-group app", workflow_text)
        self.assertIn("--require-group worker", workflow_text)


if __name__ == "__main__":
    unittest.main()
