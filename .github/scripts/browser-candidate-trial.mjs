// Non-required verification only. The tested checkout and event SHA are distinct.
import { createHash, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const source = "0b453698515e90c651707dd1f38bd498d9db95b9";
const projects = ["desktop", "phone", "phone-webkit"];
const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
const [mode, directory, argument] = process.argv.slice(2);
const sourceRoot = mode === "prepare" ? resolve("..") : resolve(argument);
const { regressionIdentity, buildVerificationInventory, BROWSER_REGRESSION } = await import(pathToFileURL(join(sourceRoot, ".github/scripts/verify-browser-evidence.mjs")));
const { verifyProjectEvidence } = await import(pathToFileURL(join(sourceRoot, ".github/scripts/browser-project-evidence.mjs")));
const sourceGit = (...args) => git("-C", sourceRoot, ...args);
if (sourceGit("rev-parse", "HEAD") !== source) throw new Error("Wrong candidate checkout");
const { GITHUB_SHA, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, ImageOS, ImageVersion } = process.env;
if (!/^[0-9a-f]{40}$/.test(GITHUB_SHA ?? "") || !/^[1-9]\d*$/.test(GITHUB_RUN_ID ?? "")
  || !/^[1-9]\d*$/.test(GITHUB_RUN_ATTEMPT ?? "") || !ImageOS || !ImageVersion) throw new Error("Missing wrapper provenance");
const lock = readFileSync(join(sourceRoot, "anteroom/package-lock.json"));
const expected = {
  sha: source, tree: sourceGit("rev-parse", "HEAD^{tree}"),
  workflowBlob: sourceGit("rev-parse", "HEAD:.github/workflows/ci.yml"),
  trial: GITHUB_SHA, run: GITHUB_RUN_ID, attempt: GITHUB_RUN_ATTEMPT,
  imageOS: ImageOS, imageVersion: ImageVersion, node: process.version, arch: process.arch,
  playwright: JSON.parse(lock).packages["node_modules/@playwright/test"].version,
  npmLock: hash(lock), pythonLock: hash(readFileSync(join(sourceRoot, "anteroom/uv.lock"))),
};

if (mode === "prepare") {
  if (!projects.includes(argument)) throw new Error("Unknown project");
  if (read(join(sourceRoot, "anteroom/node_modules/@playwright/test/package.json")).version !== expected.playwright) throw new Error("Unlocked Playwright");
  mkdirSync(directory, { recursive: true });
  const key = randomBytes(32);
  writeFileSync(join(directory, "identity-key"), key, { flag: "wx", mode: 0o600 });
  writeFileSync(join(directory, "metadata.json"), JSON.stringify({ ...expected, project: argument, regression: regressionIdentity(key) }), { flag: "wx" });
} else if (mode === "verify") {
  const records = projects.map((project) => {
    const path = join(directory, `trial-evidence-${project}-${expected.attempt}`);
    return { metadata: read(join(path, "metadata.json")), full: read(join(path, "full.json")), cases: read(join(path, "executed.json")) };
  });
  const { full, collections, counts } = verifyProjectEvidence(records, expected, process.env.PROJECTS_RESULT);
  // Exercise the candidate's strict historical WebKit regression binding too.
  const inventory = buildVerificationInventory(full, collections, {
    regression: records[2].metadata.regression,
    target: source, tree: expected.tree, ciBlob: expected.workflowBlob,
    runId: expected.run, attempt: expected.attempt,
    regressionFileBlob: sourceGit("rev-parse", `HEAD:${BROWSER_REGRESSION.repoFile}`),
  });
  writeFileSync("trial-summary.json", JSON.stringify({
    release_evidence: false, provenance: expected, total: inventory.cases.length,
    projects: counts, cases: inventory.cases,
  }, null, 2) + "\n");
  writeFileSync("candidate-browser-inventory.json", JSON.stringify({ ...inventory, release_evidence: false, wrapper_sha: GITHUB_SHA }, null, 2) + "\n");
  console.log(`Verified exact candidate ${source}: ${inventory.cases.length} cases, all three project partitions, strict WebKit regression passed. Non-required verification only.`);
} else throw new Error("Use prepare or verify");
