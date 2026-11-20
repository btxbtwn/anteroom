import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const projects = ["desktop", "phone", "phone-webkit"];
const outcomes = ["passed", "failed", "skipped", "timedOut"];
const digest = /^[0-9a-f]{64}$/;
const limit = 1024 * 1024;

function inventory(cases, executed) {
  if (!Array.isArray(cases) || !cases.length) throw new Error("Empty inventory");
  const entries = new Map();
  for (const row of cases) {
    const { id, file, project, expected, attempts } = row;
    if (typeof id !== "string" || typeof file !== "string" || !digest.test(id) || !digest.test(file)
      || !projects.includes(project) || entries.has(id)
      || !["passed", "failed", "skipped"].includes(expected) || !Array.isArray(attempts)
      || attempts.some(({ status, duration, retry }, index) => !outcomes.includes(status)
        || !Number.isFinite(duration) || duration < 0 || retry !== index)
      || (executed ? !attempts.length || attempts.at(-1).status !== expected : attempts.length > 0)) {
      throw new Error("Invalid, duplicate or incomplete case");
    }
    entries.set(id, { project, file, expected });
  }
  return entries;
}

export function verifyCollections(full, first, second, actual) {
  const complete = inventory(full, false);
  const shards = [inventory(first, false), inventory(second, false)];
  const recorded = inventory(actual, true);
  const sameCase = (id, row) => {
    const original = complete.get(id);
    return original?.project === row.project && original.file === row.file;
  };
  if (projects.some((project) => !full.some((row) => row.project === project))
    || shards[0].size + shards[1].size !== complete.size || recorded.size !== complete.size
    || [...shards[0]].some(([id]) => shards[1].has(id))
    || [...recorded].some(([id, row]) => !sameCase(id, row))
    || shards.some((cases) => [...cases].some(([id, row]) =>
      !sameCase(id, row) || complete.get(id).expected !== row.expected))) {
    throw new Error("Sandbox collection parity failed");
  }

  return Object.fromEntries(projects.map((project) => {
    const cases = actual.filter((row) => row.project === project);
    return [project, {
      collected: cases.length,
      shardCounts: shards.map((shard) => cases.filter(({ id }) => shard.has(id)).length),
      skipped: cases.filter(({ expected }) => expected === "skipped").length,
      expectedFailures: cases.filter(({ expected }) => expected === "failed").length,
      retried: cases.filter(({ attempts }) => attempts.length > 1).length,
    }];
  }));
}

function readInventory(directory, name) {
  const path = join(directory, name + ".json");
  if (statSync(path).size > limit) throw new Error("Inventory too large");
  return JSON.parse(readFileSync(path, "utf8"));
}

function main(directory) {
  const full = readInventory(directory, "full");
  const first = readInventory(directory, "shard-1");
  const second = readInventory(directory, "shard-2");
  const actual = readInventory(directory, "actual");
  const counts = verifyCollections(full, first, second, actual);

  const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
  const sha = git("rev-parse", "HEAD");
  const { GITHUB_SHA, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, ImageOS, ImageVersion } = process.env;
  if (sha !== GITHUB_SHA || !/^[1-9]\d*$/.test(GITHUB_RUN_ID)
    || !/^[1-9]\d*$/.test(GITHUB_RUN_ATTEMPT) || !ImageOS || !ImageVersion) {
    throw new Error("Missing or mismatched browser evidence provenance");
  }
  const npmLock = readFileSync("package-lock.json");
  const playwright = JSON.parse(readFileSync("node_modules/@playwright/test/package.json")).version;
  if (playwright !== JSON.parse(npmLock).packages["node_modules/@playwright/test"].version) {
    throw new Error("Playwright version differs from lockfile");
  }
  const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
  const firstIds = new Set(first.map(({ id }) => id));
  const evidence = JSON.stringify({
    schema: 1,
    provenance: {
      sha, tree: git("rev-parse", "HEAD^{tree}"),
      workflowBlob: git("rev-parse", "HEAD:.github/workflows/ci.yml"),
      run: GITHUB_RUN_ID, attempt: GITHUB_RUN_ATTEMPT, imageOS: ImageOS, imageVersion: ImageVersion,
      playwright, node: process.version, arch: process.arch,
      npmLock: sha256(npmLock), pythonLock: sha256(readFileSync("uv.lock")),
    },
    counts,
    // Reconstruct the publication from approved fields only.
    cases: actual.map(({ id, project, file, expected, attempts }) => ({
      id, project, file, expected, shard: firstIds.has(id) ? 1 : 2,
      attempts: attempts.map(({ status, duration, retry }) => ({ status, duration, retry })),
    })),
  });
  if (Buffer.byteLength(evidence) > limit) throw new Error("Evidence too large");
  writeFileSync(join(directory, "profile.json"), evidence, { flag: "wx" });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    main(process.argv[2]);
  } catch {
    console.error("Sandbox browser evidence unavailable. See the collection and test step results.");
    process.exitCode = 1;
  }
}
