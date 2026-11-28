import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { source, projects, verifyTrial } from "./browser-ci-trial.mjs";
import { verifyCollections } from "./verify-browser-evidence.mjs";

function fixture() {
  const full = projects.map((project, index) => ({
    id: String(index + 1).repeat(64), file: "a".repeat(64), spec: String(index + 4).repeat(64),
    project, expected: "passed", attempts: [],
  }));
  return projects.map((project, index) => ({
    metadata: {
      sha: source, tree: "tree", workflowBlob: "blob", trial: "trial", run: "1", attempt: "1",
      imageOS: "ubuntu24", imageVersion: "version", node: "22", arch: "x64",
      playwright: "version", npmLock: "npm", pythonLock: "python", project,
    },
    full: structuredClone(full),
    executed: [{ ...structuredClone(full[index]), attempts: [{ status: "passed", duration: 10, retry: 0 }] }],
  }));
}

test("validates three-project union and retains retries and runtime skips", () => {
  const records = fixture();
  records[0].executed[0].attempts = [
    { status: "failed", duration: 8, retry: 0 }, { status: "passed", duration: 10, retry: 1 },
  ];
  records[1].executed[0].expected = "skipped";
  records[1].executed[0].attempts[0].status = "skipped";
  const result = verifyTrial(records, verifyCollections);
  assert.equal(result.total, 3);
  assert.equal(result.projects.desktop.retried, 1);
  assert.equal(result.projects.phone.skipped, 1);
  assert.equal(result.cases[0].attempts.length, 2);
});

for (const [name, alter] of [
  ["missing project", (r) => r.pop()],
  ["duplicate project", (r) => { r[1] = structuredClone(r[0]); }],
  ["missing case", (r) => { r[0].executed = []; }],
  ["duplicate case", (r) => { r[0].executed.push(r[0].executed[0]); }],
  ["wrong project", (r) => { r[0].executed[0].project = "phone"; }],
  ["different inventory", (r) => { r[0].full[0].spec = "b".repeat(64); }],
  ["failed case", (r) => { r[0].executed[0].attempts[0].status = "failed"; }],
  ["missing attempt", (r) => { r[0].executed[0].attempts = []; }],
  ["invalid retry", (r) => { r[0].executed[0].attempts[0].retry = 1; }],
  ["wrong source", (r) => { r.forEach((x) => { x.metadata.sha = "0".repeat(40); }); }],
  ["different input", (r) => { r[1].metadata.npmLock = "different"; }],
  ["missing provenance", (r) => { r.forEach((x) => { delete x.metadata.trial; }); }],
]) {
  test(`rejects ${name}`, () => {
    const records = fixture();
    alter(records);
    assert.throws(() => verifyTrial(records, verifyCollections));
  });
}

test("trial workflow stays branch-only, read-only and separate from deploy/CI", () => {
  const workflow = readFileSync(new URL("../workflows/browser-ci-trial.yml", import.meta.url), "utf8");
  assert.match(workflow, /name: Browser topology trial/);
  assert.match(workflow, /branches: \[2348-browser-ci-comparison\]/);
  assert.match(workflow, /contents: read/);
  const candidate = readFileSync(new URL("./browser-candidate-trial.mjs", import.meta.url), "utf8");
  const target = candidate.match(/const source = "([0-9a-f]{40})";/)[1];
  assert.match(workflow, new RegExp(`SOURCE: ${target}`));
  assert.doesNotMatch(workflow, /^\s*GITHUB_SHA:/m);
  assert.doesNotMatch(workflow, /^\s*(secrets|environment|workflow_call|workflow_run|pull_request|workflow_dispatch):/m);
  assert.doesNotMatch(workflow, /:\s*write\b|secrets\.|flyctl|gh pr|--admin/);
  assert.match(workflow, /PROJECTS_RESULT:.*needs\.projects\.result/);
  assert.match(workflow, /test "\$PROJECTS_RESULT" = success/);
});
