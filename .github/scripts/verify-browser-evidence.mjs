import { createHash, createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const projects = ["desktop", "phone", "phone-webkit"];
const outcomes = ["passed", "failed", "skipped", "timedOut"];
const digest = /^[0-9a-f]{64}$/;
const gitSha = /^[0-9a-f]{40}$/;
const limit = 1024 * 1024;

export const BROWSER_REGRESSION = Object.freeze({
  project: "phone-webkit",
  relativeFile: "chatStreaming.e2e.ts",
  repoFile: "anteroom/e2e/chatStreaming.e2e.ts",
  title: "a reply delivered in large chunks is revealed word by word",
});

function inventory(cases, executed, requireSpec = false) {
  if (!Array.isArray(cases) || !cases.length) throw new Error("Empty inventory");
  const entries = new Map();
  for (const row of cases) {
    const { id, file, project, expected, attempts, spec } = row;
    const validSpec = spec === undefined
      ? !requireSpec
      : typeof spec === "string" && digest.test(spec);
    if (typeof id !== "string" || typeof file !== "string" || !digest.test(id) || !digest.test(file)
      || !validSpec || !projects.includes(project) || entries.has(id)
      || !["passed", "failed", "skipped"].includes(expected) || !Array.isArray(attempts)
      || attempts.some(({ status, duration, retry }, index) => !outcomes.includes(status)
        || !Number.isFinite(duration) || duration < 0 || retry !== index)
      || (executed ? !attempts.length || attempts.at(-1).status !== expected : attempts.length > 0)) {
      throw new Error("Invalid, duplicate or incomplete case");
    }
    entries.set(id, { project, file, expected, spec });
  }
  return entries;
}

export function verifyCollections(full, collections, requireSpec = false) {
  const complete = inventory(full, false, requireSpec);
  const shards = collections.map((cases) => inventory(cases, true, requireSpec));
  const actual = collections.flat();
  const recorded = inventory(actual, true, requireSpec);
  const sameCase = (id, row) => {
    const original = complete.get(id);
    return original?.project === row.project && original.file === row.file
      && original.spec === row.spec
      && (original.expected === "passed" || original.expected === row.expected);
  };
  if (projects.some((project) => !full.some((row) => row.project === project))
    || recorded.size !== complete.size
    || [...recorded].some(([id, row]) => !sameCase(id, row))) {
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

export function verifyProjectCollections(records) {
  if (!Array.isArray(records) || records.length !== projects.length) throw new Error("Missing project evidence");
  const full = [];
  const collections = projects.map((project) => {
    const matches = records.filter((record) => record.project === project);
    if (matches.length !== 1) throw new Error("Missing or duplicate project evidence");
    const [record] = matches;
    inventory(record.full, false, true);
    if (projects.some((name) => !record.full.some((row) => row.project === name))
      || !Array.isArray(record.cases) || record.cases.some((row) => row.project !== project)) {
      throw new Error("Wrong project evidence");
    }
    // Each runner keeps its own private key. Compare its execution against its
    // own unfiltered collection, then combine the three disjoint projects.
    full.push(...record.full.filter((row) => row.project === project));
    return record.cases;
  });
  return { full, collections, counts: verifyCollections(full, collections, true) };
}

export function regressionIdentity(key) {
  if (!Buffer.isBuffer(key) || key.length !== 32) throw new Error("Invalid evidence key");
  const hash = (kind, value) => createHmac("sha256", key).update(kind + "\0" + value).digest("hex");
  return {
    file: hash("file", BROWSER_REGRESSION.relativeFile),
    spec: hash("spec", `${BROWSER_REGRESSION.project}\0${BROWSER_REGRESSION.relativeFile}\0${BROWSER_REGRESSION.title}`),
  };
}

export function buildVerificationInventory(full, collections, {
  regression,
  target,
  tree,
  ciBlob,
  runId,
  attempt,
  regressionFileBlob,
}) {
  if (!regression || typeof regression.file !== "string" || typeof regression.spec !== "string"
    || !digest.test(regression.file) || !digest.test(regression.spec)) {
    throw new Error("Invalid regression identity");
  }
  for (const sha of [target, tree, ciBlob, regressionFileBlob]) {
    if (typeof sha !== "string" || !gitSha.test(sha)) {
      throw new Error("Invalid Git SHA provenance for browser verification inventory");
    }
  }
  if (!/^[1-9]\d*$/.test(String(runId ?? "")) || !/^[1-9]\d*$/.test(String(attempt ?? ""))) {
    throw new Error("Invalid run/attempt provenance for browser verification inventory");
  }
  verifyCollections(full, collections, true);
  const actual = collections.flat();
  const expectedFile = regression.file;
  const expectedSpec = regression.spec;
  const fullMatches = full.filter((row) => row.spec === expectedSpec);
  const actualMatches = actual.filter((row) => row.spec === expectedSpec);
  if (fullMatches.length !== 1 || actualMatches.length !== 1 || fullMatches[0].id !== actualMatches[0].id) {
    throw new Error("Expected exactly one phone-webkit chatStreaming regression case across full and executed inventories");
  }
  const [matchedFull] = fullMatches;
  const [matchedActual] = actualMatches;
  if (matchedFull.project !== BROWSER_REGRESSION.project
    || matchedFull.file !== expectedFile
    || matchedFull.expected !== "passed"
    || matchedActual.project !== BROWSER_REGRESSION.project
    || matchedActual.file !== expectedFile
    || matchedActual.expected !== "passed"
    || matchedActual.attempts.length !== 1
    || matchedActual.attempts[0].status !== "passed"
    || matchedActual.attempts[0].retry !== 0) {
    throw new Error("The historical phone-webkit chatStreaming regression must pass on first attempt without skip or retry");
  }
  const shards = new Map(collections.flatMap((rows, index) => rows.map(({ id }) => [id, index + 1])));
  const regressionKeySha256 = createHash("sha256")
    .update(`${BROWSER_REGRESSION.project}\0${BROWSER_REGRESSION.repoFile}\0${BROWSER_REGRESSION.title}`)
    .digest("hex");
  return {
    schema: 1,
    target,
    tree,
    ci_blob: ciBlob,
    run_id: String(runId),
    attempt: String(attempt),
    regression_file: BROWSER_REGRESSION.repoFile,
    regression_file_blob: regressionFileBlob,
    regression_key_sha256: regressionKeySha256,
    regression_case_id: matchedActual.id,
    full: full.map(({ id, project, file, expected }) => ({ id, project, file, expected })),
    cases: actual.map(({ id, project, file, expected, attempts }) => ({
      id,
      project,
      file,
      shard: shards.get(id),
      expected,
      attempts: attempts.map(({ status, duration, retry }) => ({ status, duration, retry })),
    })),
  };
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
  const actual = [...first, ...second];
  const counts = verifyCollections(full, [first, second]);
  console.log(`Verified ${actual.length} sandbox cases across both shards.`);
  const verificationOutput = process.env.VERIFICATION_BROWSER_INVENTORY_PATH || "";
  if (process.env.GITHUB_ACTIONS !== "true") {
    if (verificationOutput) {
      throw new Error("Verification browser inventory requires GitHub Actions provenance");
    }
    return;
  }

  const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
  const sha = git("rev-parse", "HEAD");
  const tree = git("rev-parse", "HEAD^{tree}");
  const workflowBlob = git("rev-parse", "HEAD:.github/workflows/ci.yml");
  const { GITHUB_SHA, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, ImageOS, ImageVersion } = process.env;
  if (sha !== GITHUB_SHA || !/^[1-9]\d*$/.test(GITHUB_RUN_ID)
    || !/^[1-9]\d*$/.test(GITHUB_RUN_ATTEMPT) || !/^[a-zA-Z0-9._-]{1,40}$/.test(ImageOS ?? "")
    || !/^[a-zA-Z0-9._-]{1,40}$/.test(ImageVersion ?? "")) {
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
      sha, tree,
      workflowBlob,
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

  if (verificationOutput) {
    const key = readFileSync(join(directory, "identity-key"));
    const regressionFileBlob = git("rev-parse", `HEAD:${BROWSER_REGRESSION.repoFile}`);
    const payload = JSON.stringify(buildVerificationInventory(full, [first, second], {
      regression: regressionIdentity(key),
      target: sha,
      tree,
      ciBlob: workflowBlob,
      runId: GITHUB_RUN_ID,
      attempt: GITHUB_RUN_ATTEMPT,
      regressionFileBlob,
    }));
    if (Buffer.byteLength(payload) > limit) throw new Error("Verification browser inventory too large");
    writeFileSync(verificationOutput, payload + "\n", { flag: "wx" });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    main(process.argv[2]);
  } catch {
    console.error("Sandbox browser evidence unavailable. See the collection and test step results.");
    process.exitCode = 1;
  }
}
