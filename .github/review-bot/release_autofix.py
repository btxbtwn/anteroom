"""Review a failed main-branch release and publish a validated fix as a PR."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from review_bot import (
    api,
    git,
    is_ancestor,
    protected_changes,
    repo,
    run,
    set_output,
    truncate_log,
)

DEPLOY = ".github/workflows/deploy-hosted.yml"
MAC = ".github/workflows/studio-macos.yml"
PIPELINES = {
    DEPLOY: ("Deploy hosted", "deploy-hosted", {"workflow_run", "workflow_dispatch"}),
    MAC: ("Studio macOS", "studio-macos", {"push", "workflow_dispatch"}),
}
# These steps run builds or checks without production/signing secrets.
# For other steps pass the name and, when matched, a fixed diagnostic only.
SAFE_LOG_STEPS = {
    DEPLOY: {
        "Choose the newest tested commit on main",
        "Wait for CI on the code being shipped",
        "Install the hosted server",
        "Confirm the live version",
    },
    MAC: {
        "Install dependencies and build frontend",
        "Exercise the packaged app on sandbox data",
        "Verify native Keychain updates preserve the existing item",
        "Verify update zip symlinks are safe",
    },
}
NOTARY_STEPS = {"Package app", "Package standalone subscription connector"}
FAILURES = {"failure", "timed_out"}
SHA = re.compile(r"[0-9a-f]{40}")
REPAIR_BRANCH = re.compile(r"claude/release-fix/(deploy-hosted|studio-macos)/[0-9a-f]{40}")


def eligible(source: dict, attempt: int) -> bool:
    pipeline = PIPELINES.get(source.get("path"))
    return (
        pipeline is not None
        and source.get("event") in pipeline[2]
        and source.get("head_branch") == "main"
        and (source.get("head_repository") or {}).get("full_name") == repo()
        and source.get("run_attempt") == attempt
        and source.get("status") == "completed"
        and source.get("conclusion") in FAILURES
    )


def failure_evidence(path: str, jobs: list[dict], log: str) -> tuple[dict, str]:
    failed_jobs = [job for job in jobs if job["conclusion"] in FAILURES]
    failed = [
        (job["name"], step["name"])
        for job in failed_jobs
        for step in job.get("steps", ()) if step["conclusion"] in FAILURES
    ]
    lines = []
    diagnostics = set()
    for line in log.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue
        job, step, message = fields
        if (job, step) not in failed:
            continue
        if step in SAFE_LOG_STEPS[path]:
            lines.append(line)
        elif path == MAC and step in NOTARY_STEPS and "NSURLErrorDomain Code=-1001" in message:
            diagnostics.add("Apple's notarization request timed out (NSURLErrorDomain -1001).")
    return {
        "failed_jobs": [job["name"] for job in failed_jobs],
        "failed_steps": [f"{job} / {step}" for job, step in failed],
        "diagnostics": sorted(diagnostics),
    }, truncate_log("\n".join(lines))


def review_dir() -> Path:
    return Path(os.environ["RELEASE_REVIEW_DIR"])


def source_path(run_id: int) -> str:
    return f"repos/{repo()}/actions/runs/{run_id}"


def run_url(run_id: int, attempt: int) -> str:
    return f"https://github.com/{repo()}/actions/runs/{run_id}/attempts/{attempt}"


def repair_pr(branch: str) -> str:
    return run(
        "gh", "pr", "list", "--repo", repo(), "--state", "all", "--head", branch,
        "--json", "url", "--jq", ".[0].url // empty",
    ).strip()


def deploy_target(jobs: list[dict]) -> str | None:
    targets = set()
    for job in jobs:
        if job["name"] != "Migrate and deploy":
            continue
        for note in api(f"repos/{repo()}/check-runs/{job['id']}/annotations"):
            if note.get("title") == "Deploy target" and SHA.fullmatch(note.get("message") or ""):
                targets.add(note["message"])
    return targets.pop() if len(targets) == 1 else None


def prepare() -> None:
    run_id = int(os.environ["SOURCE_RUN"])
    attempt = int(os.environ["SOURCE_ATTEMPT"])
    endpoint = source_path(run_id)
    source = api(endpoint)
    if not eligible(source, attempt):
        set_output(skip="true")
        return

    path = source["path"]
    name, slug, _ = PIPELINES[path]
    jobs = api(f"{endpoint}/attempts/{attempt}/jobs?per_page=100")["jobs"]
    target = deploy_target(jobs) if path == DEPLOY else source["head_sha"]
    base = git("rev-parse", "HEAD").strip()
    if target and (not SHA.fullmatch(target) or not is_ancestor(target, base)):
        target = None
    branch = f"claude/release-fix/{slug}/{target}" if target else None
    if branch and (existing := repair_pr(branch)):
        print(f"A repair PR already exists for this revision: {existing}")
        set_output(skip="true")
        return

    try:
        log = run("gh", "run", "view", str(run_id), "--repo", repo(), "--attempt", str(attempt), "--log-failed")
    except subprocess.CalledProcessError:
        log = ""
    evidence, safe_log = failure_evidence(path, jobs, log)
    context = {
        "run_id": run_id,
        "attempt": attempt,
        "url": run_url(run_id, attempt),
        "pipeline": name,
        "workflow_file": path,
        "failed_revision": target,
        "repair_branch": branch,
        "base": base,
        **evidence,
    }
    (review_dir() / "context.json").write_text(json.dumps(context, indent=2) + "\n")
    (review_dir() / "failed.log").write_text(safe_log or "(No eligible detailed logs for these steps.)\n")
    set_output(skip="false", can_repair="true" if branch else "false")


def context_and_report() -> tuple[dict, str]:
    context = json.loads((review_dir() / "context.json").read_text())
    report_file = review_dir() / "report.md"
    report = report_file.read_text().strip()[:6000] if report_file.is_file() and not report_file.is_symlink() else ""
    return context, report


def publish() -> None:
    context, report = context_and_report()
    branch = context["repair_branch"]
    if not branch or not REPAIR_BRANCH.fullmatch(branch) or not report:
        sys.exit("A known failed revision and a validation report are required.")
    if not eligible(api(source_path(context["run_id"])), context["attempt"]):
        sys.exit("The failed attempt was superseded.")
    if existing := repair_pr(branch):
        (review_dir() / "repair-pr.txt").write_text(existing)
        print(existing)
        return
    if git("rev-parse", "HEAD").strip() != context["base"]:
        sys.exit("The checkout moved during review.")

    git("add", "--all")
    paths = git("diff", "--cached", "--no-renames", "--name-only", "-z").rstrip("\0").split("\0")
    if not paths[0]:
        sys.exit("There is no source fix to publish.")
    if protected_changes(paths) or any(p.startswith(".claude/") or p == ".mcp.json" for p in paths):
        sys.exit("The proposed fix changes protected files.")
    git("diff", "--cached", "--check")
    git("commit", "-m", f"Fix failure in {context['pipeline']}")
    # The Claude App token starts the normal PR checks. Push a new ref only.
    git("push", f"--force-with-lease=refs/heads/{branch}:", "origin", f"HEAD:refs/heads/{branch}")
    (review_dir() / "branch-pushed").touch()
    title = f"Fix {context['pipeline']} failure at {context['failed_revision'][:12]}"
    body = f"Failed run: {context['url']}\n\n{report}"
    issue = api(
        f"repos/{repo()}/issues", "POST",
        {"title": title, "body": f"{body}\n\nDone when: The failed check passes with the repair."},
    )
    pr = api(
        f"repos/{repo()}/pulls", "POST",
        {"title": title, "body": f"Closes #{issue['number']}\n\n{body}", "head": branch, "base": "main"},
    )
    (review_dir() / "repair-pr.txt").write_text(pr["html_url"])
    print(pr["html_url"])


def summarize() -> None:
    context, report = context_and_report()
    pr = review_dir() / "repair-pr.txt"
    if pr.is_file():
        result = f"[Repair PR]({pr.read_text()})"
    elif (review_dir() / "branch-pushed").is_file():
        url = f"https://github.com/{repo()}/compare/main...{context['repair_branch']}"
        result = f"PR creation did not finish. [Open the proposed fix]({url})."
    else:
        result = "No repair PR was opened."
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"## {context['pipeline']} failure review\n\n")
        summary.write(f"[Failed attempt]({context['url']})\n\n{result}\n\n")
        summary.writelines(f"- {step}\n" for step in context["failed_steps"] or context["failed_jobs"])
        summary.write("\n")
        summary.write((report or "Claude did not produce a diagnosis.") + "\n")


if __name__ == "__main__":
    {"prepare": prepare, "publish": publish, "summarize": summarize}[sys.argv[1]]()
