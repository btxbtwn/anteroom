import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import BrowserEvidenceReporter from "./browser-evidence-reporter.mjs";
import { executionNode, rerunGuidance, verifyProjectEvidence } from "./browser-project-evidence.mjs";
import { spawnSync } from "node:child_process";
import {
  BROWSER_REGRESSION,
  buildVerificationInventory,
  regressionIdentity,
  verifyCollections,
} from "./verify-browser-evidence.mjs";

const full = ["desktop", "phone", "phone-webkit"].map((project, index) => ({
  id: String(index).repeat(64), file: "a".repeat(64), project, expected: "passed", attempts: [],
}));
const attempt = (status, retry = 0) => ({ status, duration: 12, retry });
const actual = [
  { ...full[0], attempts: [attempt("failed"), attempt("passed", 1)] },
  { ...full[1], expected: "skipped", attempts: [attempt("skipped")] },
  { ...full[2], attempts: [attempt("passed")] },
];
const first = actual.slice(0, 2);
const second = actual.slice(2);

test("retains all three projects, retries and runtime skips", () => {
  const counts = verifyCollections(full, [first, second]);
  assert.equal(counts.desktop.retried, 1);
  assert.equal(counts.phone.skipped, 1);
  assert.deepEqual(counts["phone-webkit"].shardCounts, [0, 1]);
});

function projectEvidence() {
  const metadata = { sha: "a".repeat(40), tree: "b".repeat(40), workflowBlob: "c".repeat(40),
    run: "42", attempt: "1", npmLock: "npm", pythonLock: "python", playwright: "pw", node: executionNode,
    arch: "x64", imageOS: "ubuntu26", imageVersion: "image" };
  return ["desktop", "phone", "phone-webkit"].map((project, index) => {
    const key = Buffer.alloc(32, index + 1);
    const hash = (kind, value) => createHmac("sha256", key).update(kind + "\0" + value).digest("hex");
    const full = ["desktop", "phone", "phone-webkit"].map((name) => ({
      id: hash("case", name), file: hash("file", BROWSER_REGRESSION.relativeFile), project: name,
      spec: hash("spec", `${name}\0${BROWSER_REGRESSION.relativeFile}\0${BROWSER_REGRESSION.title}`),
      expected: "passed", attempts: [],
    }));
    return { metadata: { ...metadata, project, regression: regressionIdentity(key) }, full,
      cases: full.filter((row) => row.project === project).map((row) => ({ ...row, attempts: [attempt("passed")] })) };
  });
}

test("three independent private namespaces preserve exact union and verification source binding", () => {
  const records = projectEvidence();
  assert.notEqual(records[0].full[0].id, records[1].full[0].id);
  const expected = { ...records[0].metadata };
  delete expected.project;
  delete expected.regression;
  const { full: complete, collections, counts } = verifyProjectEvidence(records, expected, "success");
  assert.equal(complete.length, 3);
  assert.deepEqual(counts["phone-webkit"].shardCounts, [0, 0, 1]);
  const receipt = buildVerificationInventory(complete, collections, {
    regression: records[2].metadata.regression, target: expected.sha, tree: expected.tree,
    ciBlob: expected.workflowBlob, runId: expected.run, attempt: expected.attempt,
    regressionFileBlob: "0f040c3572c8c5a9c1e024c70be72dc11fcbc5f9",
  });
  assert.equal(receipt.cases[2].shard, 3);
  assert.equal(receipt.target, expected.sha);
});

test("aggregate runtime drift does not change the vetted execution runtime", () => {
  const records = projectEvidence();
  const expected = { ...records[0].metadata, node: "v22.23.6", imageVersion: "next-image", imageOS: "ubuntu28", arch: "arm64" };
  delete expected.project;
  delete expected.regression;
  assert.equal(verifyProjectEvidence(records, expected, "success").full.length, 3);
  for (const [field, value] of [["node", "v22.23.6"], ["arch", "arm64"], ["imageOS", "ubuntu28"]]) {
    const unvetted = projectEvidence();
    for (const record of unvetted) record.metadata[field] = value;
    assert.throws(() => verifyProjectEvidence(unvetted, expected, "success"), /unvetted/);
  }
});

test("CLI failures explain the same-attempt rerun requirement", () => {
  const result = spawnSync(process.execPath, [new URL("./browser-project-evidence.mjs", import.meta.url).pathname, "verify", "/missing-browser-artifacts"], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.ok(result.stderr.includes(rerunGuidance));
});

test("matrix evidence fails closed on missing jobs, mismatched inputs and incomplete collections", () => {
  const mutations = [
    (r) => r.pop(),
    (r) => { r[1] = structuredClone(r[0]); },
    (r) => { r[2].cases = []; },
    (r) => { r[2].cases.push(r[2].cases[0]); },
    (r) => { r[2].cases[0].project = "phone"; },
    (r) => { r[2].cases[0].attempts = []; },
    (r) => { r[2].cases[0].attempts[0].status = "failed"; },
    (r) => { r[2].full.pop(); },
    (r) => { r[2].full[0].attempts = [attempt("passed")]; },
  ];
  for (const key of ["sha", "tree", "workflowBlob", "run", "attempt", "npmLock", "pythonLock", "playwright", "node", "arch", "imageOS", "imageVersion"]) {
    mutations.push((r) => { r[2].metadata[key] = "different"; });
  }
  for (const mutate of mutations) {
    const records = projectEvidence();
    const expected = { ...records[0].metadata };
    delete expected.project;
    delete expected.regression;
    mutate(records);
    assert.throws(() => verifyProjectEvidence(records, expected, "success"));
  }
  for (const status of ["failure", "cancelled", "skipped", "", undefined]) {
    assert.throws(() => verifyProjectEvidence(projectEvidence(), {}, status));
  }
});

test("existing browser and required CI gates depend on the project matrix and preserve diagnostics", () => {
  const text = readFileSync(new URL("../workflows/ci.yml", import.meta.url), "utf8");
  const gate = text.split("\n  studio-browser:\n")[1].split("\n  studio-code-health:")[0];
  assert.match(gate, /name: Studio browser tests \(\$\{\{ matrix.suite.name \}\}\)/);
  assert.match(gate, /needs: \[changes, studio-browser-projects, studio-browser-hosted\]/);
  assert.match(gate, /if: always\(\) && needs.changes.result == 'success'/);
  assert.match(gate, /PROJECTS_RESULT: \$\{\{ needs.studio-browser-projects.result \}\}/);
  assert.match(gate, /HOSTED_RESULT: \$\{\{ needs.studio-browser-hosted.result \}\}/);
  assert.match(gate, /test "\$HOSTED_RESULT" = success/);
  assert.match(gate, /pattern: sandbox-browser-project-\*-attempt-\$\{\{ github.run_attempt \}\}/);
  assert.doesNotMatch(gate, /^\s+(token|repository|run-id):/m);
  const matrix = text.split("\n  studio-browser-projects:\n")[1].split("\n  studio-browser-hosted:\n")[0];
  assert.match(matrix, /project: \[desktop, phone, phone-webkit\]/);
  assert.ok(matrix.includes(`node-version: "${executionNode.slice(1)}"`));
  assert.match(matrix, /fail-fast: false/);
  assert.match(matrix, /timeout-minutes: 40/);
  assert.match(matrix, /sandbox-browser\/\*\.json/);
  assert.doesNotMatch(matrix, /identity-key\s*$/m);
  assert.doesNotMatch(matrix, /secrets\.|^\s*environment:|:\s*write\b/m);
  assert.match(matrix, /studio-browser-report-sandbox-\$\{\{ matrix.project \}\}/);
  assert.match(matrix, /if: always\(\)/);
  assert.match(matrix, /retention-days: 7/);
  const hosted = text.split("\n  studio-browser-hosted:\n")[1].split("\n  studio-browser:\n")[0];
  assert.match(hosted, /needs: changes/);
  assert.doesNotMatch(hosted, /needs:.*studio-browser-projects/);
  assert.match(hosted, /run: npm run e2e:hosted/);
});

test("rejects missing, duplicated, foreign or relabeled cases", () => {
  for (const shard of [
    [], [first[0]], [...first, second[0]], [first[0], first[0]],
    [first[0], { ...first[1], id: "b".repeat(64) }],
    [first[0], { ...first[1], project: "desktop" }],
    [first[0], { ...first[1], file: "b".repeat(64) }],
    [first[0], { ...first[1], id: [first[1].id] }],
  ]) {
    assert.throws(() => verifyCollections(full, [shard, second]));
  }
});

test("rejects incomplete or unsuccessful execution and malformed durations", () => {
  for (const attempts of [
    [], [attempt("interrupted")], [attempt("failed")],
    [{ ...attempt("passed"), duration: Infinity }], [attempt("passed", 1)],
  ]) {
    assert.throws(() => verifyCollections(full, [[{ ...first[0], attempts }, first[1]], second]));
  }
});

test("reporter includes only hashes and approved attempt metadata", (t) => {
  const directory = mkdtempSync(join(tmpdir(), "browser-evidence-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const output = join(directory, "profile.json");
  const previous = process.env.SANDBOX_BROWSER_EVIDENCE_FILE;
  process.env.SANDBOX_BROWSER_EVIDENCE_FILE = output;
  t.after(() => {
    if (previous === undefined) delete process.env.SANDBOX_BROWSER_EVIDENCE_FILE;
    else process.env.SANDBOX_BROWSER_EVIDENCE_FILE = previous;
  });
  const reporter = new BrowserEvidenceReporter();
  const sentinel = "private-value-sentinel";
  reporter.onBegin({ rootDir: directory }, { allTests: () => [{
    id: sentinel,
    title: sentinel,
    titlePath: () => ["", "phone", sentinel, sentinel],
    parent: { project: () => ({ name: "phone" }) },
    location: { file: join(directory, sentinel) },
    expectedStatus: "passed",
    results: [{ ...attempt("passed"), error: sentinel, stdout: [sentinel] }],
  }] });
  const key = join(directory, "identity-key");
  reporter.onEnd({ status: "passed" });
  assert.throws(() => readFileSync(output), { code: "ENOENT" });
  writeFileSync(key, Buffer.alloc(32, 1));
  reporter.onEnd({ status: "interrupted" });
  assert.throws(() => readFileSync(output), { code: "ENOENT" });
  reporter.onEnd({ status: "passed" });
  const evidence = readFileSync(output, "utf8");
  assert.ok(!evidence.includes(sentinel));
  const recorded = JSON.parse(evidence)[0];
  assert.deepEqual(recorded.attempts, [attempt("passed")]);
  assert.match(recorded.spec, /^[0-9a-f]{64}$/);
  for (const byte of [1, 2]) {
    writeFileSync(key, Buffer.alloc(32, byte));
    const nextOutput = join(directory, `probe-${byte}.json`);
    process.env.SANDBOX_BROWSER_EVIDENCE_FILE = nextOutput;
    reporter.onEnd({ status: "passed" });
    const next = JSON.parse(readFileSync(nextOutput))[0];
    assert.equal(next.id === recorded.id, byte === 1);
    assert.equal(next.file === recorded.file, byte === 1);
    assert.equal(next.spec === recorded.spec, byte === 1);
  }
});

test("verification inventory proves phone-webkit chatStreaming regression passed and fails closed on skip/mismatch", () => {
  const key = Buffer.alloc(32, 7);
  const hash = (kind, value) => createHmac("sha256", key).update(kind + "\0" + value).digest("hex");
  const makeCase = (project, relFile, title, idSeed) => ({
    id: hash("case", idSeed),
    project,
    file: hash("file", relFile),
    spec: hash("spec", `${project}\0${relFile}\0${title}`),
    expected: "passed",
    attempts: [],
  });
  const baseFull = [
    makeCase("desktop", "chatStreaming.e2e.ts", BROWSER_REGRESSION.title, "d-1"),
    makeCase("phone", "chatStreaming.e2e.ts", BROWSER_REGRESSION.title, "p-1"),
    makeCase("phone-webkit", BROWSER_REGRESSION.relativeFile, BROWSER_REGRESSION.title, "pw-1"),
  ];
  const baseFirst = [
    { ...baseFull[0], attempts: [attempt("passed")] },
    { ...baseFull[1], expected: "skipped", attempts: [attempt("skipped")] },
  ];
  const baseSecond = [
    { ...baseFull[2], attempts: [attempt("passed")] },
  ];
  const meta = {
    regression: regressionIdentity(key),
    target: "a".repeat(40),
    tree: "b".repeat(40),
    ciBlob: "c".repeat(40),
    runId: "42",
    attempt: "1",
    regressionFileBlob: "0f040c3572c8c5a9c1e024c70be72dc11fcbc5f9",
  };

  const payload = buildVerificationInventory(baseFull, [baseFirst, baseSecond], meta);
  assert.equal(payload.schema, 1);
  assert.equal(payload.regression_case_id, baseFull[2].id);
  assert.equal(payload.regression_file, BROWSER_REGRESSION.repoFile);
  assert.equal(payload.cases.length, 3);
  assert.ok(!JSON.stringify(payload).includes(BROWSER_REGRESSION.title));

  // Retried pass (failed attempt 0 then passed retry 1) must fail closed.
  assert.throws(() => buildVerificationInventory(
    baseFull,
    [baseFirst,
    [{ ...baseFull[2], attempts: [attempt("failed"), attempt("passed", 1)] }]],
    meta,
  ), /first attempt/);

  // Skipped regression case must be rejected even when full collection passed.
  assert.throws(() => buildVerificationInventory(
    baseFull,
    [baseFirst,
    [{ ...baseFull[2], expected: "skipped", attempts: [attempt("skipped")] }]],
    meta,
  ), /first attempt/);

  // Missing regression case (e.g., different title on phone-webkit) must be rejected.
  const wrongTitle = makeCase("phone-webkit", BROWSER_REGRESSION.relativeFile, "other test", "pw-1");
  assert.throws(() => buildVerificationInventory(
    [baseFull[0], baseFull[1], wrongTitle],
    [baseFirst,
    [{ ...wrongTitle, attempts: [attempt("passed")] }]],
    meta,
  ));

  // Missing spec hash or tampered provenance must be rejected.
  assert.throws(() => buildVerificationInventory(
    baseFull.map(({ spec: _s, ...rest }) => rest),
    [baseFirst.map(({ spec: _s, ...rest }) => rest),
    baseSecond.map(({ spec: _s, ...rest }) => rest)],
    meta,
  ));
  assert.throws(() => regressionIdentity(Buffer.alloc(16, 1)));
  assert.throws(() => buildVerificationInventory(baseFull, [baseFirst, baseSecond], { ...meta, regression: null }));
  assert.throws(() => buildVerificationInventory(baseFull, [baseFirst, baseSecond], { ...meta, regressionFileBlob: "short" }));
});
