import { randomBytes, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { BROWSER_REGRESSION, regressionIdentity, verifyProjectCollections, buildVerificationInventory } from "./verify-browser-evidence.mjs";

export const projects = ["desktop", "phone", "phone-webkit"];
const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const read = (path) => {
  if (statSync(path).size > 1024 * 1024) throw new Error("Evidence too large");
  return JSON.parse(readFileSync(path, "utf8"));
};

function sourceIdentity() {
  const { GITHUB_SHA, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, ImageOS, ImageVersion } = process.env;
  const sha = git("rev-parse", "HEAD");
  if (process.env.GITHUB_ACTIONS !== "true" || sha !== GITHUB_SHA
    || !/^[1-9]\d*$/.test(GITHUB_RUN_ID ?? "") || !/^[1-9]\d*$/.test(GITHUB_RUN_ATTEMPT ?? "")
    || !/^[a-zA-Z0-9._-]{1,40}$/.test(ImageOS ?? "") || !/^[a-zA-Z0-9._-]{1,40}$/.test(ImageVersion ?? "")) {
    throw new Error("Missing or mismatched GitHub source/run provenance");
  }
  const lock = readFileSync("package-lock.json");
  return {
    sha, tree: git("rev-parse", "HEAD^{tree}"), workflowBlob: git("rev-parse", "HEAD:.github/workflows/ci.yml"),
    run: GITHUB_RUN_ID, attempt: GITHUB_RUN_ATTEMPT, imageOS: ImageOS, imageVersion: ImageVersion,
    node: process.version, arch: process.arch,
    playwright: JSON.parse(lock).packages["node_modules/@playwright/test"].version,
    npmLock: hash(lock), pythonLock: hash(readFileSync("uv.lock")),
  };
}

export function verifyProjectEvidence(records, expected, dependencyResult) {
  if (dependencyResult !== "success") throw new Error("Every browser project must succeed");
  if (!Array.isArray(records) || records.length !== projects.length) throw new Error("Missing project artifacts");
  for (const record of records) {
    if (!record.metadata || Object.entries(expected).some(([key, value]) => record.metadata[key] !== value)) {
      throw new Error("Browser source, workflow, locks, runtime or attempt mismatch");
    }
  }
  return verifyProjectCollections(records.map(({ metadata, full, cases }) => ({ project: metadata.project, full, cases })));
}

function main() {
  const [mode, directory, project] = process.argv.slice(2);
  const expected = sourceIdentity();
  if (mode === "prepare") {
    if (!projects.includes(project)) throw new Error("Unknown browser project");
    if (read("node_modules/@playwright/test/package.json").version !== expected.playwright) throw new Error("Unlocked Playwright");
    mkdirSync(directory, { recursive: true });
    const key = randomBytes(32);
    writeFileSync(join(directory, "identity-key"), key, { flag: "wx", mode: 0o600 });
    writeFileSync(join(directory, "metadata.json"), JSON.stringify({ ...expected, project, regression: regressionIdentity(key) }), { flag: "wx" });
    return;
  }
  if (mode !== "verify") throw new Error("Use prepare or verify");
  const records = projects.map((name) => {
    const path = join(directory, `sandbox-browser-project-${name}-attempt-${expected.attempt}`);
    return { metadata: read(join(path, "metadata.json")), full: read(join(path, "full.json")), cases: read(join(path, "executed.json")) };
  });
  const { full, collections, counts } = verifyProjectEvidence(records, expected, process.env.PROJECTS_RESULT);
  const cases = collections.flatMap((rows, index) => rows.map(({ id, project: name, file, expected: status, attempts }) => ({
    id, project: name, file, expected: status, shard: index + 1,
    attempts: attempts.map(({ status: outcome, duration, retry }) => ({ status: outcome, duration, retry })),
  })));
  mkdirSync(join(directory, "evidence"));
  writeFileSync(join(directory, "evidence/profile.json"), JSON.stringify({ schema: 1, provenance: expected, counts, cases }), { flag: "wx" });
  if (process.env.VERIFICATION_BROWSER_INVENTORY_PATH) {
    const payload = buildVerificationInventory(full, collections, {
      regression: records.find(({ metadata }) => metadata.project === BROWSER_REGRESSION.project).metadata.regression,
      target: expected.sha, tree: expected.tree, ciBlob: expected.workflowBlob,
      runId: expected.run, attempt: expected.attempt,
      regressionFileBlob: git("rev-parse", `HEAD:${BROWSER_REGRESSION.repoFile}`),
    });
    writeFileSync(process.env.VERIFICATION_BROWSER_INVENTORY_PATH, JSON.stringify(payload) + "\n", { flag: "wx" });
  }
  console.log(`Verified ${cases.length} sandbox cases across all three browser projects.`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
