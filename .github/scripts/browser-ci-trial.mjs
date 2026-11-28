// Experimental evidence only. Never consumed by required CI or deployment.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

export const source = "7f81013122e8e50636c3d3e516757536408e2dcc";
export const projects = ["desktop", "phone", "phone-webkit"];
const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const hash = (data) => createHash("sha256").update(data).digest("hex");

export function verifyTrial(records, verifyCollections) {
  if (records.length !== 3) throw new Error("Expected all three project artifacts");
  const reference = records[0].metadata;
  const fields = ["sha", "tree", "workflowBlob", "trial", "run", "attempt", "imageOS",
    "imageVersion", "node", "arch", "playwright", "npmLock", "pythonLock"];
  for (const field of fields) {
    if (typeof reference[field] !== "string" || !reference[field]) throw new Error(`Missing ${field}`);
  }
  if (reference.sha !== source) throw new Error("Wrong source");
  const order = (rows) => [...rows].sort((a, b) => a.id.localeCompare(b.id));
  const inventory = JSON.stringify(order(records[0].full));
  const actual = [];
  for (const project of projects) {
    const matches = records.filter(({ metadata }) => metadata.project === project);
    if (matches.length !== 1) throw new Error("Missing or duplicate project artifact");
    const [record] = matches;
    if (fields.some((field) => record.metadata[field] !== reference[field])) throw new Error("Mismatched inputs");
    if (JSON.stringify(order(record.full)) !== inventory) throw new Error("Different full inventories");
    if (!record.executed.length || record.executed.some((row) => row.project !== project)) {
      throw new Error("Wrong project execution");
    }
    actual.push(...record.executed);
  }
  const desktop = actual.filter((row) => row.project === "desktop");
  const others = actual.filter((row) => row.project !== "desktop");
  // Reuse the source validator's exact case, expected outcome, retry sequence,
  // duplicate and completeness checks. The grouping here is only an adapter.
  const counts = verifyCollections(records[0].full, desktop, others, true);
  return {
    provenance: Object.fromEntries(fields.map((field) => [field, reference[field]])),
    total: actual.length,
    projects: Object.fromEntries(projects.map((project) => {
      const { shardCounts: _unused, ...count } = counts[project];
      return [project, count];
    })),
    cases: actual,
  };
}

function main() {
  const [mode, directory, argument] = process.argv.slice(2);
  if (mode === "prepare") {
    if (!projects.includes(argument)) throw new Error("Unknown project");
    const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
    const sha = git("rev-parse", "HEAD");
    if (sha !== source) throw new Error("Checkout is not the pinned baseline source");
    const lock = readFileSync("package-lock.json");
    const playwright = read("node_modules/@playwright/test/package.json").version;
    if (playwright !== JSON.parse(lock).packages["node_modules/@playwright/test"].version) throw new Error("Unlocked Playwright");
    const metadata = {
      sha, tree: git("rev-parse", "HEAD^{tree}"), workflowBlob: git("rev-parse", "HEAD:.github/workflows/ci.yml"),
      trial: process.env.GITHUB_SHA, run: process.env.GITHUB_RUN_ID, attempt: process.env.GITHUB_RUN_ATTEMPT,
      imageOS: process.env.ImageOS, imageVersion: process.env.ImageVersion,
      node: process.version, arch: process.arch, playwright,
      npmLock: hash(lock), pythonLock: hash(readFileSync("uv.lock")), project: argument,
    };
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, "metadata.json"), JSON.stringify(metadata));
    // Common identity namespace across this one run. Not a credential; never
    // used as trusted release evidence or joined to another run's salted IDs.
    writeFileSync(join(directory, "identity-key"), Buffer.from(hash(`${metadata.run}:${metadata.attempt}:${sha}`), "hex"));
  } else throw new Error("Use prepare or verify");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (process.argv[2] === "verify") {
    const directory = process.argv[3];
    const records = projects.map((project) => {
      const path = join(directory, `trial-evidence-${project}-${process.env.GITHUB_RUN_ATTEMPT}`);
      return { metadata: read(join(path, "metadata.json")), full: read(join(path, "full.json")), executed: read(join(path, "executed.json")) };
    });
    const { verifyCollections } = await import(pathToFileURL(resolve(process.argv[4], ".github/scripts/verify-browser-evidence.mjs")));
    const summary = verifyTrial(records, verifyCollections);
    if (summary.provenance.trial !== process.env.GITHUB_SHA || summary.provenance.run !== process.env.GITHUB_RUN_ID
      || summary.provenance.attempt !== process.env.GITHUB_RUN_ATTEMPT) throw new Error("Wrong trial run or attempt");
    writeFileSync("trial-summary.json", JSON.stringify(summary, null, 2) + "\n");
    console.log(`Verified ${summary.total} cases across all three projects.`);
  } else main();
}
