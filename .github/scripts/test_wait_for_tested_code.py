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
        self.committed_correction = self.gate.HISTORICAL_RECOVERY_CORRECTION
        self.committed_failures = self.gate.HISTORICAL_RECOVERY_FAILURES
        self.repo = "example/project"
        self.prod = "1" * 40
        self.before_hist = "2" * 40
        self.hist = verification.HISTORICAL
        self.second_hist = "ab" * 20
        self.corr = verification.CORRECTED
        patcher = patch.multiple(
            self.gate, HISTORICAL_RECOVERY_CORRECTION=self.corr,
            HISTORICAL_RECOVERY_FAILURES=frozenset({self.hist, self.second_hist}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.target = "3" * 40
        self.post_merge = "4" * 40
        self.head = "5" * 40
        self.off_branch = "6" * 40
        self.tree = "7" * 40
        self.wf_blob = "8" * 40
        self.ci_blob = "9" * 40

        self.span_to_target = [self.before_hist, self.hist, self.second_hist, self.corr, self.target]
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
                [self.before_hist, self.hist, self.second_hist, self.corr]
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
            historical_failure=self.hist,
            correcting_merge=self.corr,
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

    def test_committed_recovery_spans_the_three_split_head_windows(self):
        pin = self.gate.HISTORICAL_RECOVERY_CHECKPOINT
        self.assertIsNotNone(pin)
        self.assertEqual(pin.production_base, "23f8ab0110c1ef359949462996c321acdbc92c49")
        self.assertEqual(pin.historical_failure, verification.HISTORICAL)
        self.assertEqual(pin.correcting_merge, verification.CORRECTED)
        self.assertEqual(self.committed_correction, "684c73b7618390a92555666589d7b0c8f6efec8b")
        self.assertEqual(self.committed_failures, {
            "99c3b7003d1d79cfc20cea283bebde7e54572081",
            "08b97e99edaa02f021bc7bcf876d2fd2ade83c69",
            "fb7d5c076cce6021b48240014ba6fb0e71256300",
            "1e3756a043eb56aed73ff72b9401da5076533f64",
            "7d9b51611d4923565d817e172170bb70d7d05f8b",
            "55c4ceaf7b767cd9094881f2730013a62d33e29c",
            "8e9a08803af54e159495ed80f3ca0c2735fde36e",
            "8265f86e3639860a9560088b7d50fe212a186cc4",
            "ab40d2ef7120f16747246c6ee20aa52a32fd92ba",
        })
        self.assertEqual(
            self.gate.HISTORICAL_BASE_IMAGE_DIGEST,
            "sha256:22df137ee877807504d7223d6aff53e8cee6777cf2ddfd70838a14a822641591",
        )
        self.assertEqual(self.gate.HISTORICAL_BASE_DB_REVISION, "e3b7a1c9d402")
        self.assertEqual(self.gate.HISTORICAL_BASE_FLY_APP, "peaceful-meadowstone-6944")

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

    def test_allowlisted_failures_must_all_precede_the_correction(self):
        with patch.object(self.gate, "HISTORICAL_RECOVERY_FAILURES", {self.hist, self.post_merge}):
            eligible, reason = self.gate.checkpoint_eligible(
                self.repo, self.head, self.prod, self.hist, pin=self.pin, git_fn=self.fake_git
            )
        self.assertFalse(eligible)
        self.assertIn("must precede the correcting merge", reason)

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
            if sha in (self.hist, self.second_hist):
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
                return_value=([self.before_hist, self.hist, self.second_hist, self.corr, self.post_merge], "span"),
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
        base_revision = self.gate.HISTORICAL_BASE_DB_REVISION
        live_state = {
            "recorded_production": self.prod,
            "health_version": "0.1.1011",
            "machines": self._base_machines(),
            "db_revision": base_revision,
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
        self.assertIn(base_revision, err2)
        self.assertEqual(effects, [], "Attempt 2 must fail closed before any build/migration/rollout effect")

        # Worker drifted to new image while web still reports 0.1.1011 and DB was reset -> still fails closed.
        live_state["db_revision"] = base_revision
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


class WaitForTestedCodePushCoverageTests(unittest.TestCase):
    def setUp(self):
        self.gate = _load_wait_gate()
        self.repo = "example/project"
        self.prod = "1" * 40
        self.c1 = "2" * 40
        self.c2 = "3" * 40
        self.tip = "4" * 40
        self.release = "5" * 40
        self.prod_tree = "a" * 40
        self.tip_tree = "b" * 40
        self.run_id = 44684943388
        self.attempt = 1
        self.check_suite_id = 120952562240

        self.full_span = [self.c1, self.c2, self.tip, self.release]
        self.all_checked = [self.c1, self.c2, self.tip]
        self.push_span = [self.c1, self.c2, self.tip]
        self.changed_files = [
            "anteroom/server/anteroom/app.py",
            "anteroom/src/App.tsx",
            "changelog.d/fix.md",
        ]

        self.git_values = {
            ("rev-parse", self.release): self.release,
            ("rev-parse", self.tip): self.tip,
            ("rev-parse", f"{self.prod}^{{tree}}"): self.prod_tree,
            ("rev-parse", f"{self.tip}^{{tree}}"): self.tip_tree,
            ("rev-parse", f"{self.c1}^1"): self.prod,
            ("rev-list", "--first-parent", "--reverse", f"{self.prod}..{self.release}"): "\n".join(self.full_span),
            ("rev-list", "--first-parent", "--reverse", f"{self.prod}..{self.tip}"): "\n".join(self.push_span),
            ("diff", "--no-renames", "--name-only", "-z", self.prod, self.tip): "\0".join(self.changed_files) + "\0",
            ("ls-tree", "--name-only", self.tip): ".github\nanteroom\nanteroom-extension\nchangelog.d\ndocs\n",
        }

        self.run = {
            "id": self.run_id,
            "run_attempt": self.attempt,
            "check_suite_id": self.check_suite_id,
            "event": "push",
            "head_branch": "main",
            "head_sha": self.tip,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "repository": {"id": 1183824317, "full_name": self.repo},
            "head_repository": {"id": 1183824317, "full_name": self.repo},
            "head_commit": {"id": self.tip, "tree_id": self.tip_tree},
        }
        self.latest_run = None
        self.suite = {
            "id": self.check_suite_id,
            "app": {"slug": "github-actions"},
            "head_branch": "main",
            "head_sha": self.tip,
            "before": self.prod,
            "after": self.tip,
            "status": "completed",
            "conclusion": "failure",  # Unrelated workflow in same check suite must not poison CI check.
        }
        self.compare = {
            "status": "ahead",
            "behind_by": 0,
            "ahead_by": len(self.push_span),
            "total_commits": len(self.push_span),
            "base_commit": {"sha": self.prod, "commit": {"tree": {"sha": self.prod_tree}}},
            "merge_base_commit": {"sha": self.prod, "commit": {"tree": {"sha": self.prod_tree}}},
            "commits": [
                {"sha": self.c1, "commit": {"tree": {"sha": "c" * 40}}},
                {"sha": self.c2, "commit": {"tree": {"sha": "d" * 40}}},
                {"sha": self.tip, "commit": {"tree": {"sha": self.tip_tree}}},
            ],
            "files": [{"filename": path, "status": "modified"} for path in self.changed_files],
        }
        self.jobs = self._make_jobs(
            succeeded=[
                "Changed components",
                "Changelog",
                "Repo checks",
                "CI",
                "Studio frontend",
                "Studio backend (1/2)",
                "Studio backend (2/2)",
                "Studio backend Postgres (1/2)",
                "Studio backend Postgres (2/2)",
                "Studio browser tests (sandbox)",
                "Studio browser tests (hosted)",
                "Studio code health",
            ],
            skipped=["Extension", "Dependency audit", "Webhook relay"],
        )
        self.fetch_calls: list[str] = []

    def _make_jobs(self, *, succeeded: list[str], skipped: list[str] = ()) -> list[dict]:
        items = []
        idx = 1
        for name in succeeded:
            items.append({
                "id": idx,
                "run_id": self.run_id,
                "run_attempt": self.attempt,
                "head_sha": self.tip,
                "name": name,
                "status": "completed",
                "conclusion": "success",
            })
            idx += 1
        for name in skipped:
            items.append({
                "id": idx,
                "run_id": self.run_id,
                "run_attempt": self.attempt,
                "head_sha": self.tip,
                "name": name,
                "status": "completed",
                "conclusion": "skipped",
            })
            idx += 1
        return items

    def fake_git(self, *args: str) -> str:
        return self.git_values[args]

    def fake_fetch(self, path: str):
        self.fetch_calls.append(path)
        if path == f"actions/workflows/ci.yml/runs?head_sha={self.c2}&event=push&per_page=1":
            return {"workflow_runs": []}
        if path == f"actions/workflows/ci.yml/runs?head_sha={self.tip}&event=push&per_page=1":
            return {"workflow_runs": [copy.deepcopy(self.run)]}
        if path == f"actions/runs/{self.run_id}":
            return copy.deepcopy(self.run if self.latest_run is None else self.latest_run)
        if path == f"check-suites/{self.check_suite_id}":
            return copy.deepcopy(self.suite)
        if path in (f"compare/{self.prod}...{self.tip}", f"compare/{self.prod}...{self.tip}?per_page=100&page=1"):
            return copy.deepcopy(self.compare)
        if path.startswith(f"actions/runs/{self.run_id}/attempts/{self.attempt}/jobs?"):
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        raise AssertionError(f"Unexpected API path: {path}")

    def test_valid_multi_commit_push_covers_intermediates_and_uses_cache(self):
        cache: dict[str, dict] = {}
        state1, detail1 = self.gate.verify_push_coverage(
            self.repo,
            self.c1,
            self.all_checked,
            self.prod,
            self.release,
            fetch=self.fake_fetch,
            git_fn=self.fake_git,
            cache=cache,
        )
        self.assertEqual(state1, "covered")
        self.assertIn(self.tip[:8], detail1)
        self.assertIn(str(self.run_id), detail1)
        calls_after_first = len(self.fetch_calls)

        state2, _ = self.gate.verify_push_coverage(
            self.repo,
            self.c2,
            self.all_checked,
            self.prod,
            self.release,
            fetch=self.fake_fetch,
            git_fn=self.fake_git,
            cache=cache,
        )
        self.assertEqual(state2, "covered")
        self.assertEqual(len(self.fetch_calls), calls_after_first, "Tip evaluation must be cached within one poll")

    def test_waiting_failed_and_toctou_rerun_states(self):
        self.run["status"] = "in_progress"
        self.run["conclusion"] = None
        state, detail = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "waiting")
        self.assertIn("in_progress", detail)

        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.run["status"] = "completed"
                self.run["conclusion"] = conclusion
                state, _ = self.gate.verify_push_coverage(
                    self.repo, self.c1, self.all_checked, self.prod, self.release,
                    fetch=self.fake_fetch, git_fn=self.fake_git,
                )
                self.assertEqual(state, "failed")

        # TOCTOU: run re-triggered to attempt 2 while attempt 1 jobs were read.
        self.run["status"] = "completed"
        self.run["conclusion"] = "success"
        self.latest_run = {**copy.deepcopy(self.run), "run_attempt": 2, "status": "in_progress", "conclusion": None}
        state, detail = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "waiting")
        self.assertIn("attempt", detail)

    def test_untrusted_push_provenance_fails_closed(self):
        mutations = [
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("head_branch", "feature"),
            ("path", ".github/workflows/other.yml"),
            ("head_sha", self.c2),
            ("repository", {"id": 1183824317, "full_name": "evil/project"}),
            ("head_repository", {"id": 999999, "full_name": self.repo}),
            ("head_repository", {"id": 1183824317, "full_name": "fork/project"}),
            ("head_commit", {"id": self.tip, "tree_id": "f" * 40}),
            ("head_commit", {"id": self.c2, "tree_id": self.tip_tree}),
            ("id", True),
            ("run_attempt", 0),
            ("check_suite_id", -1),
        ]
        for key, bad_value in mutations:
            with self.subTest(key=key, bad_value=bad_value):
                orig = copy.deepcopy(self.run)
                self.run[key] = bad_value
                state, _ = self.gate.verify_push_coverage(
                    self.repo, self.c1, self.all_checked, self.prod, self.release,
                    fetch=self.fake_fetch, git_fn=self.fake_git,
                )
                self.run = orig
                self.assertEqual(state, "missing")

    def test_check_suite_and_ancestry_mismatches_fail_closed(self):
        # Wrong check suite fields.
        suite_mutations = [
            ("id", self.check_suite_id + 1),
            ("app", {"slug": "other-app"}),
            ("head_branch", "other"),
            ("head_sha", self.c2),
            ("after", self.c2),
            ("before", "0" * 40),
            ("before", self.tip),
            ("before", "e" * 40),  # Outside production..HEAD span.
        ]
        for key, bad_value in suite_mutations:
            with self.subTest(key=key, bad_value=bad_value):
                orig = copy.deepcopy(self.suite)
                self.suite[key] = bad_value
                state, _ = self.gate.verify_push_coverage(
                    self.repo, self.c1, self.all_checked, self.prod, self.release,
                    fetch=self.fake_fetch, git_fn=self.fake_git,
                )
                self.suite = orig
                self.assertEqual(state, "missing")

        # Single-commit push at tip (before == c2) cannot cover c1 or c2.
        self.suite["before"] = self.c2
        self.git_values[("rev-parse", f"{self.c2}^{{tree}}")] = "d" * 40
        self.git_values[("rev-list", "--first-parent", "--reverse", f"{self.c2}..{self.tip}")] = self.tip
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "missing")

        # First-parent gap: c1^1 != before fails closed.
        self.suite["before"] = self.prod
        self.git_values[("rev-parse", f"{self.c1}^1")] = "f" * 40
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "missing")

    def test_compare_diff_mismatches_fail_closed_and_renames_match(self):
        mutations = [
            ("status", "diverged"),
            ("behind_by", 1),
            ("ahead_by", 2),
            ("total_commits", 4),
            ("base_commit", {"sha": self.c1, "commit": {"tree": {"sha": self.prod_tree}}}),
            ("merge_base_commit", {"sha": self.c1, "commit": {"tree": {"sha": self.prod_tree}}}),
            ("base_commit", {"sha": self.prod, "commit": {"tree": {"sha": "f" * 40}}}),
            ("commits", self.compare["commits"][1:]),
            (
                "commits",
                [
                    self.compare["commits"][0],
                    self.compare["commits"][1],
                    {"sha": self.tip, "commit": {"tree": {"sha": "f" * 40}}},
                ],
            ),
            ("files", [{"filename": "anteroom/server/anteroom/app.py", "status": "modified"}]),
            (
                "files",
                [*self.compare["files"], {"filename": "anteroom/uv.lock", "status": "modified"}],
            ),
        ]
        for key, bad_value in mutations:
            with self.subTest(key=key):
                orig = copy.deepcopy(self.compare)
                self.compare[key] = bad_value
                state, _ = self.gate.verify_push_coverage(
                    self.repo, self.c1, self.all_checked, self.prod, self.release,
                    fetch=self.fake_fetch, git_fn=self.fake_git,
                )
                self.compare = orig
                self.assertEqual(state, "missing")

        # GitHub Compare renames include previous_filename; matches git diff --no-renames.
        renamed_local = [
            "anteroom/server/anteroom/new_name.py",
            "anteroom/server/anteroom/old_name.py",
            "anteroom/src/App.tsx",
        ]
        self.git_values[("diff", "--no-renames", "--name-only", "-z", self.prod, self.tip)] = (
            "\0".join(renamed_local) + "\0"
        )
        self.compare["files"] = [
            {
                "filename": "anteroom/server/anteroom/new_name.py",
                "previous_filename": "anteroom/server/anteroom/old_name.py",
                "status": "renamed",
            },
            {"filename": "anteroom/src/App.tsx", "status": "modified"},
        ]
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "covered")

    def test_missing_skipped_failed_or_audit_jobs_fail_closed(self):
        # Required job skipped -> fail closed.
        orig_jobs = copy.deepcopy(self.jobs)
        for job in self.jobs:
            if job["name"] == "Studio backend Postgres (2/2)":
                job["conclusion"] = "skipped"
        state, detail = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")
        self.assertIn("Studio backend Postgres (2/2)", detail)

        # Unselected job failed -> fail closed.
        self.jobs = copy.deepcopy(orig_jobs)
        for job in self.jobs:
            if job["name"] == "Extension":
                job["conclusion"] = "failure"
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")

        # Duplicate job name or mismatched job attempt -> fail closed.
        self.jobs = copy.deepcopy(orig_jobs)
        self.jobs[0]["run_attempt"] = 2
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")

        # Dependency change in uv.lock requires reusable Dependency audit job name.
        self.jobs = copy.deepcopy(orig_jobs)
        lock_files = ["anteroom/uv.lock"]
        self.git_values[("diff", "--no-renames", "--name-only", "-z", self.prod, self.tip)] = "anteroom/uv.lock\0"
        self.git_values[("show", f"{self.prod}:anteroom/uv.lock")] = 'name = "anteroom"\nversion = "0.1.1"\ndep = "1.0"\n'
        self.git_values[("show", f"{self.tip}:anteroom/uv.lock")] = 'name = "anteroom"\nversion = "0.1.1"\ndep = "2.0"\n'
        self.compare["files"] = [{"filename": path, "status": "modified"} for path in lock_files]
        state, detail = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")
        self.assertIn("Dependency audit / Audit locked Python dependencies", detail)

        # Once the exact reusable audit job succeeded, coverage passes.
        for job in self.jobs:
            if job["name"] == "Dependency audit":
                job["name"] = "Dependency audit / Audit locked Python dependencies"
                job["conclusion"] = "success"
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "covered")

    def test_pre_rename_normalization_applies_only_to_historical_pre_rename_tree(self):
        pre_files = [
            "vendoo-studio/server/vendoo_studio/app.py",
            "vendoo-studio/src/App.tsx",
            "changelog.d/fix.md",
        ]
        self.git_values[("diff", "--no-renames", "--name-only", "-z", self.prod, self.tip)] = (
            "\0".join(pre_files) + "\0"
        )
        self.compare["files"] = [{"filename": path, "status": "modified"} for path in pre_files]

        # Post-rename tree (`anteroom` present): unknown vendoo-studio/ paths select ALL components and fail closed
        # because Extension / Dependency audit / Webhook relay were skipped.
        state, detail = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")
        self.assertIn("Extension", detail)

        # Pre-rename tree (`vendoo-studio` present, `anteroom` absent): normalizes to anteroom/ and passes exact 12 checks.
        self.git_values[("ls-tree", "--name-only", self.tip)] = ".github\nvendoo-studio\nvendoo-extension\nchangelog.d\n"
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "covered")

    def test_main_handles_covered_waiting_and_failed_rebase_push(self):
        # 1. Valid multi-commit push: c1 and c2 missing, tip passed -> exits 0 with recovery_checkpoint=false.
        with tempfile.TemporaryDirectory() as tmp:
            gh_out = Path(tmp) / "github_output"
            with (
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(gh_out)}),
                patch.object(self.gate, "git", side_effect=self.fake_git),
                patch.object(self.gate, "_read_git", side_effect=self.fake_git),
                patch.object(self.gate, "production_commit", return_value=self.prod),
                patch.object(self.gate, "commits_to_check", return_value=(self.all_checked, "span")),
                patch.object(
                    self.gate, "ci_state",
                    side_effect=lambda r, s: ("completed", "success") if s == self.tip else ("missing", ""),
                ),
                patch.object(
                    self.gate, "verify_push_coverage",
                    side_effect=lambda repo, sha, all_c, prod, head, **kw: ("covered", "verified push CI"),
                ),
                patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.release, "--timeout", "0"]),
            ):
                self.assertEqual(self.gate.main(), 0)
            self.assertEqual(gh_out.read_text(encoding="utf-8"), "recovery_checkpoint=false\n")

        # 2. Covering push initially in_progress, then completes -> waits and exits 0.
        polls = {"n": 0}

        def fake_ci_poll(repo, sha):
            if sha == self.tip:
                polls["n"] += 1
                return ("in_progress", "") if polls["n"] == 1 else ("completed", "success")
            return ("missing", "")

        def fake_cov_poll(repo, sha, all_c, prod, head, **kw):
            return ("waiting", "push CI in_progress") if polls["n"] == 0 else ("covered", "verified push CI")

        with (
            patch.object(self.gate, "_write_github_output") as write_output,
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(self.gate, "commits_to_check", return_value=(self.all_checked, "span")),
            patch.object(self.gate, "ci_state", side_effect=fake_ci_poll),
            patch.object(self.gate, "verify_push_coverage", side_effect=fake_cov_poll),
            patch.object(self.gate.time, "sleep", return_value=None),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.release, "--timeout", "60", "--interval", "0"]),
        ):
            self.assertEqual(self.gate.main(), 0)

            write_output.assert_called_once_with("recovery_checkpoint", "false")

        # 3. Failed coverage evidence exits 1 immediately.
        with (
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(self.gate, "commits_to_check", return_value=(self.all_checked, "span")),
            patch.object(
                self.gate, "ci_state",
                side_effect=lambda r, s: ("completed", "success") if s == self.tip else ("missing", ""),
            ),
            patch.object(self.gate, "verify_push_coverage", return_value=("failed", "missing required check")),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.release, "--timeout", "60"]),
        ):
            self.assertEqual(self.gate.main(), 1)

    def test_compare_rename_entry_limit_and_truncated_subset(self):
        paths = [f"docs/{i}.md" for i in range(602)]
        self.compare["files"] = [
            {"filename": paths[i], "previous_filename": paths[i + 1]}
            for i in range(0, 302, 2)
        ]
        def validate(local):
            self.gate._validate_compare_diff(
                self.compare, before=self.prod, tip=self.tip,
                before_tree=self.prod_tree, tip_tree=self.tip_tree,
                push_span=self.push_span, local_files=local,
            )
        validate(paths[:302])  # 151 API entries represent 302 local paths.
        with self.assertRaises(ValueError):
            validate(paths[:303])  # Below cap, partial evidence is invalid.
        self.compare["files"] = [{"filename": path} for path in paths[:300]]
        validate(paths)  # At the cap, local Git supplies the complete selector input.
        self.compare["files"][0]["filename"] = "unexpected.md"
        with self.assertRaises(ValueError):
            validate(paths)

    def test_compare_paginates_251_commits_and_preserves_first_page_files(self):
        span = [f"{i:040x}" for i in range(10, 260)] + [self.tip]
        commits = [{"sha": sha, "commit": {"tree": {"sha": self.tip_tree}}} for sha in span]
        self.compare.update(ahead_by=251, total_commits=251)
        calls = []
        def fetch(path):
            calls.append(path)
            page = int(path.rsplit("=", 1)[1])
            result = {**self.compare, "commits": commits[(page - 1) * 100:page * 100]}
            if page > 1:
                result.pop("files")
            return result
        combined = self.gate._compare_pages(fetch, self.prod, self.tip, len(span))
        self.assertEqual(len(calls), 3)
        self.gate._validate_compare_diff(
            combined, before=self.prod, tip=self.tip,
            before_tree=self.prod_tree, tip_tree=self.tip_tree,
            push_span=span, local_files=self.changed_files,
        )
        combined["commits"][100] = combined["commits"][99]
        with self.assertRaises(ValueError):
            self.gate._validate_compare_diff(
                combined, before=self.prod, tip=self.tip,
                before_tree=self.prod_tree, tip_tree=self.tip_tree,
                push_span=span, local_files=self.changed_files,
            )
        with self.assertRaises(ValueError):
            self.gate._compare_pages(lambda _: self.compare, self.prod, self.tip, 251)

    def test_new_completed_attempt_requires_its_own_jobs(self):
        self.latest_run = {**self.run, "run_attempt": 2}
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "waiting")
        self.run = self.latest_run
        self.attempt = 2
        for job in self.jobs:
            job["run_attempt"] = 2
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "covered")
        self.jobs[0]["conclusion"] = "failure"
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")

    def test_reread_rejects_changed_run_or_suite(self):
        for field, value in (("id", self.run_id + 1), ("check_suite_id", self.check_suite_id + 1)):
            with self.subTest(field=field):
                self.latest_run = {**self.run, field: value, "run_attempt": 2}
                state, _ = self.gate.verify_push_coverage(
                    self.repo, self.c1, self.all_checked, self.prod, self.release,
                    fetch=self.fake_fetch, git_fn=self.fake_git,
                )
                self.assertEqual(state, "failed")

    def test_api_command_timeout_waits_but_invalid_inventory_fails(self):
        def read_command(command, **kwargs):
            if "/jobs?" in command[-1]:
                raise ValueError("Read-only command timed out.")
            return json.dumps(self.fake_fetch(command[-1].split(f"repos/{self.repo}/", 1)[1])).encode()
        with (
            patch.object(self.gate.recovery_evidence, "read_command", side_effect=read_command),
            patch.object(self.gate, "_read_git", side_effect=self.fake_git),
        ):
            state, _ = self.gate.verify_push_coverage(
                self.repo, self.c1, self.all_checked, self.prod, self.release,
            )
        self.assertEqual(state, "waiting")
        self.jobs[0]["conclusion"] = "skipped"
        state, _ = self.gate.verify_push_coverage(
            self.repo, self.c1, self.all_checked, self.prod, self.release,
            fetch=self.fake_fetch, git_fn=self.fake_git,
        )
        self.assertEqual(state, "failed")

    def test_main_revalidates_accepted_coverage_while_later_commit_waits(self):
        poll = {"n": 0}
        def ci(repo, sha):
            if sha == self.release:
                poll["n"] += 1
                return ("in_progress", "") if poll["n"] == 1 else ("completed", "success")
            return ("completed", "success") if sha == self.tip else ("missing", "")
        def coverage(*args, **kwargs):
            return ("covered", "attempt 1") if poll["n"] == 0 else ("failed", "attempt 2 failed")
        with (
            patch.object(self.gate, "_write_github_output") as output,
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(self.gate, "commits_to_check", return_value=(self.full_span, "span")),
            patch.object(self.gate, "ci_state", side_effect=ci),
            patch.object(self.gate, "verify_push_coverage", side_effect=coverage),
            patch.object(self.gate.time, "sleep", return_value=None),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.release, "--timeout", "60"]),
        ):
            self.assertEqual(self.gate.main(), 1)
            output.assert_not_called()

    def test_main_revalidates_before_success_after_direct_checks(self):
        poll = {"n": 0}
        def ci(repo, sha):
            if sha == self.release:
                poll["n"] += 1
                return ("completed", "success")
            return ("completed", "success") if sha == self.tip else ("missing", "")
        def coverage(*args, **kwargs):
            return ("covered", "attempt 1") if poll["n"] == 0 else ("failed", "attempt 2 failed")
        with (
            patch.object(self.gate, "_write_github_output") as output,
            patch.object(self.gate, "git", side_effect=self.fake_git),
            patch.object(self.gate, "production_commit", return_value=self.prod),
            patch.object(self.gate, "commits_to_check", return_value=(self.full_span, "span")),
            patch.object(self.gate, "ci_state", side_effect=ci),
            patch.object(self.gate, "verify_push_coverage", side_effect=coverage),
            patch.object(self.gate.time, "sleep", return_value=None),
            patch.object(sys, "argv", ["gate", "--repo", self.repo, "--head", self.release, "--timeout", "60"]),
        ):
            self.assertEqual(self.gate.main(), 1)
            output.assert_not_called()

    def test_ci_job_contract_matches_workflows_and_changed_components(self):
        changed_mod = self.gate._load_changed_components()
        self.assertEqual(set(self.gate.CI_COMPONENT_JOBS), set(changed_mod.COMPONENTS))
        ci_text = (SCRIPTS_DIR.parent / "workflows" / "ci.yml").read_text(encoding="utf-8")
        audit_text = (SCRIPTS_DIR.parent / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")
        for job_name in self.gate.CI_ALWAYS_REQUIRED_JOBS:
            self.assertIn(f"name: {job_name}\n", ci_text)
        self.assertIn("name: Studio frontend\n", ci_text)
        self.assertIn("name: Studio backend (${{ matrix.group }}/2)\n", ci_text)
        self.assertIn("name: Studio backend Postgres (${{ matrix.group }}/2)\n", ci_text)
        self.assertIn("name: Studio browser tests (${{ matrix.suite.name }})\n", ci_text)
        self.assertIn("name: Studio code health\n", ci_text)
        self.assertIn("name: Dependency audit\n", ci_text)
        self.assertIn("name: Audit locked Python dependencies\n", audit_text)
        self.assertIn("name: Extension\n", ci_text)
        self.assertIn("name: Webhook relay\n", ci_text)


if __name__ == "__main__":
    unittest.main()
