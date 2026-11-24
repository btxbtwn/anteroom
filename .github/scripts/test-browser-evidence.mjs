import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import BrowserEvidenceReporter from "./browser-evidence-reporter.mjs";
import { verifyCollections } from "./verify-browser-evidence.mjs";

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
  const counts = verifyCollections(full, first, second);
  assert.equal(counts.desktop.retried, 1);
  assert.equal(counts.phone.skipped, 1);
  assert.deepEqual(counts["phone-webkit"].shardCounts, [0, 1]);
});

test("rejects missing, duplicated, foreign or relabeled cases", () => {
  for (const shard of [
    [], [first[0]], [...first, second[0]], [first[0], first[0]],
    [first[0], { ...first[1], id: "b".repeat(64) }],
    [first[0], { ...first[1], project: "desktop" }],
    [first[0], { ...first[1], file: "b".repeat(64) }],
    [first[0], { ...first[1], id: [first[1].id] }],
  ]) {
    assert.throws(() => verifyCollections(full, shard, second));
  }
});

test("rejects incomplete or unsuccessful execution and malformed durations", () => {
  for (const attempts of [
    [], [attempt("interrupted")], [attempt("failed")],
    [{ ...attempt("passed"), duration: Infinity }], [attempt("passed", 1)],
  ]) {
    assert.throws(() => verifyCollections(full, [{ ...first[0], attempts }, first[1]], second));
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
    id: sentinel, parent: { project: () => ({ name: "phone" }) },
    location: { file: join(directory, sentinel) }, expectedStatus: "passed",
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
  const first = JSON.parse(evidence)[0];
  assert.deepEqual(first.attempts, [attempt("passed")]);
  for (const byte of [1, 2]) {
    writeFileSync(key, Buffer.alloc(32, byte));
    const nextOutput = join(directory, `probe-${byte}.json`);
    process.env.SANDBOX_BROWSER_EVIDENCE_FILE = nextOutput;
    reporter.onEnd({ status: "passed" });
    const next = JSON.parse(readFileSync(nextOutput))[0];
    assert.equal(next.id === first.id, byte === 1);
    assert.equal(next.file === first.file, byte === 1);
  }
});
