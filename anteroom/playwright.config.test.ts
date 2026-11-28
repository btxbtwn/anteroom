import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const ANTEROOM_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(ANTEROOM_DIR, "..");
const PLAYWRIGHT_BIN = join(
  ANTEROOM_DIR,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "playwright.cmd" : "playwright",
);

const ENV_KEYS = [
  "CI",
  "GITHUB_ACTIONS",
  "GITHUB_WORKSPACE",
  "PLAYWRIGHT_HTML_OUTPUT_DIR",
  "SANDBOX_BROWSER_EVIDENCE_FILE",
] as const;

const CONFIGS = [
  {
    label: "sandbox",
    configFile: "playwright.config.ts",
    testSubdir: "anteroom/e2e",
    supportsEvidence: true,
    defaultHtmlFolder: "e2e/report",
  },
  {
    label: "hosted",
    configFile: "playwright.hosted.config.ts",
    testSubdir: "anteroom/hosted-e2e",
    supportsEvidence: false,
    defaultHtmlFolder: "e2e/report",
  },
] as const;

type ConfigCase = (typeof CONFIGS)[number];

const tempDirs: string[] = [];

function makeTempDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function buildCleanEnv(overrides: Record<string, string | undefined>): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const key of ENV_KEYS) {
    delete env[key];
  }
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined) delete env[key];
    else env[key] = value;
  }
  return env;
}

async function loadRealConfig(
  configFile: string,
  envOverrides: Record<string, string | undefined>,
) {
  const saved = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
  for (const key of ENV_KEYS) {
    delete process.env[key];
  }
  for (const [key, value] of Object.entries(envOverrides)) {
    if (value !== undefined) process.env[key] = value;
  }
  try {
    vi.resetModules();
    const mod = configFile === "playwright.config.ts"
      ? await import("./playwright.config.ts")
      : await import("./playwright.hosted.config.ts");
    return mod.default;
  } finally {
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
}

function reporterNames(reporter: unknown): string[] {
  if (typeof reporter === "string") return [reporter];
  if (!Array.isArray(reporter)) return [];
  return reporter.map((entry) => (Array.isArray(entry) ? String(entry[0]) : String(entry)));
}

function runFixtureWithRealConfig(
  configCase: ConfigCase,
  specSource: string,
  envOverrides: Record<string, string | undefined>,
) {
  const workDir = makeTempDir(`pw-github-${configCase.label}-`);
  const specDir = join(workDir, configCase.testSubdir);
  mkdirSync(specDir, { recursive: true });
  const specPath = join(specDir, "fixture.e2e.ts");
  const reportDir = join(workDir, "html-report");
  const resultsDir = join(workDir, "results");
  const wrapperConfigPath = join(workDir, "wrapper.config.mjs");

  writeFileSync(specPath, specSource, "utf8");

  const realConfigUrl = pathToFileURL(join(ANTEROOM_DIR, configCase.configFile)).href;
  const wrapperSource = `
import { resolve } from "node:path";
import realConfig from ${JSON.stringify(realConfigUrl)};

const anteroomDir = ${JSON.stringify(ANTEROOM_DIR)};
const reporter = Array.isArray(realConfig.reporter)
  ? realConfig.reporter.map((entry) => {
      if (Array.isArray(entry) && entry[0] === "html") {
        return ["html", { ...entry[1], outputFolder: ${JSON.stringify(reportDir)} }];
      }
      if (Array.isArray(entry) && typeof entry[0] === "string" && entry[0].startsWith(".")) {
        return [resolve(anteroomDir, entry[0]), ...entry.slice(1)];
      }
      return entry;
    })
  : realConfig.reporter;

export default {
  ...realConfig,
  reporter,
  testDir: ${JSON.stringify(specDir)},
  testMatch: "fixture.e2e.ts",
  outputDir: ${JSON.stringify(resultsDir)},
  projects: [{ name: "fixture" }],
  webServer: undefined,
};
`;
  writeFileSync(wrapperConfigPath, wrapperSource, "utf8");

  const env = buildCleanEnv({
    GITHUB_WORKSPACE: workDir,
    NODE_PATH: join(ANTEROOM_DIR, "node_modules"),
    ...(configCase.label === "sandbox" ? { PLAYWRIGHT_HTML_OUTPUT_DIR: reportDir } : {}),
    ...envOverrides,
  });

  const result = spawnSync(
    PLAYWRIGHT_BIN,
    ["test", "--config", wrapperConfigPath],
    {
      cwd: ANTEROOM_DIR,
      env,
      encoding: "utf8",
      timeout: 60_000,
    },
  );

  return {
    status: result.status,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    reportIndexPath: join(reportDir, "index.html"),
    workDir,
  };
}

afterEach(() => {
  while (tempDirs.length > 0) {
    const dir = tempDirs.pop();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
});

describe("Playwright GitHub Actions reporter configuration (#2343)", () => {
  for (const configCase of CONFIGS) {
    it(`${configCase.configFile} keeps local output unchanged when CI is unset`, async () => {
      const config = await loadRealConfig(configCase.configFile, {});
      expect(config.reporter).toBe("list");
      expect(config.retries).toBe(0);
      expect(config.forbidOnly).toBe(false);
    });

    it(`${configCase.configFile} omits github reporter under local CI without GITHUB_ACTIONS=true`, async () => {
      for (const ghValue of [undefined, "", "false"]) {
        const config = await loadRealConfig(configCase.configFile, {
          CI: "true",
          GITHUB_ACTIONS: ghValue,
          ...(configCase.supportsEvidence ? { SANDBOX_BROWSER_EVIDENCE_FILE: "/tmp/evidence.json" } : {}),
        });
        expect(config.retries).toBe(1);
        expect(config.forbidOnly).toBe(true);
        const names = reporterNames(config.reporter);
        expect(names).toContain("list");
        expect(names).toContain("html");
        expect(names).not.toContain("github");
        if (configCase.supportsEvidence) {
          expect(names).toContain("../.github/scripts/browser-evidence-reporter.mjs");
        }
      }
    });

    it(`${configCase.configFile} enables github reporter under GitHub Actions while preserving existing reporters`, async () => {
      const config = await loadRealConfig(configCase.configFile, {
        CI: "true",
        GITHUB_ACTIONS: "true",
        ...(configCase.supportsEvidence ? { SANDBOX_BROWSER_EVIDENCE_FILE: "/tmp/evidence.json" } : {}),
      });
      expect(config.retries).toBe(1);
      expect(config.forbidOnly).toBe(true);
      const names = reporterNames(config.reporter);
      expect(names).toContain("list");
      expect(names).toContain("html");
      expect(names).toContain("github");
      if (configCase.supportsEvidence) {
        expect(names).toContain("../.github/scripts/browser-evidence-reporter.mjs");
      }
      const htmlEntry = (config.reporter as Array<[string, Record<string, unknown>]>).find(
        (entry) => entry[0] === "html",
      );
      expect(htmlEntry?.[1]).toEqual({
        open: "never",
        outputFolder: configCase.defaultHtmlFolder,
      });
    });

    it(`${configCase.configFile} emits file/line annotations for genuine failures and passed-on-retry tests on GitHub Actions only`, () => {
      const flakySpec = [
        'import { expect, test } from "@playwright/test";',
        "",
        'test("recovers on automatic retry", ({}, testInfo) => {',
        "  expect(testInfo.retry).toBe(1);",
        "});",
        "",
      ].join("\n");

      const failingSpec = [
        'import { expect, test } from "@playwright/test";',
        "",
        'test("fails deterministically", () => {',
        "  expect(1).toBe(2);",
        "});",
        "",
      ].join("\n");

      const expectedRepoFile = `${configCase.testSubdir}/fixture.e2e.ts`;
      const errorLinePattern = new RegExp(
        `::error [^\\n]*file=${expectedRepoFile.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^\\n]*line=4[^\\n]*::`,
      );

      // 1. Passed-on-retry on GitHub Actions: exits 0, annotates line 4, preserves list, html & evidence.
      const evidenceDir = configCase.supportsEvidence ? makeTempDir("pw-evidence-") : undefined;
      const evidenceFile = evidenceDir ? join(evidenceDir, "shard-1.json") : undefined;
      if (evidenceDir) {
        writeFileSync(join(evidenceDir, "identity-key"), randomBytes(32));
      }

      const ghFlaky = runFixtureWithRealConfig(configCase, flakySpec, {
        CI: "true",
        GITHUB_ACTIONS: "true",
        ...(evidenceFile ? { SANDBOX_BROWSER_EVIDENCE_FILE: evidenceFile } : {}),
      });
      expect(ghFlaky.status, ghFlaky.stdout + ghFlaky.stderr).toBe(0);
      expect(ghFlaky.stdout).toMatch(errorLinePattern);
      expect(ghFlaky.stdout).toContain("recovers on automatic retry");
      expect(ghFlaky.stdout).toContain("Running 1 test");
      expect(ghFlaky.stdout).toMatch(/::notice title=[^\n]*Playwright Run Summary::[^\n]*1 flaky/);
      expect(existsSync(ghFlaky.reportIndexPath)).toBe(true);
      if (evidenceFile) {
        const recorded = JSON.parse(readFileSync(evidenceFile, "utf8"));
        expect(recorded).toHaveLength(1);
        expect(recorded[0].attempts.map((a: { status: string; retry: number }) => [a.status, a.retry])).toEqual([
          ["failed", 0],
          ["passed", 1],
        ]);
      }

      // 2. Genuine failure on GitHub Actions: exits 1, annotates line 4, preserves list & html, emits summary.
      const ghFail = runFixtureWithRealConfig(configCase, failingSpec, {
        CI: "true",
        GITHUB_ACTIONS: "true",
      });
      expect(ghFail.status, ghFail.stdout + ghFail.stderr).toBe(1);
      expect(ghFail.stdout).toMatch(errorLinePattern);
      expect(ghFail.stdout).toContain("fails deterministically");
      expect(ghFail.stdout).toContain("Running 1 test");
      expect(ghFail.stdout).toMatch(/::notice title=[^\n]*Playwright Run Summary::[^\n]*1 failed/);
      expect(existsSync(ghFail.reportIndexPath)).toBe(true);

      // 3. Local ./check.sh mode (CI=true, no GITHUB_ACTIONS): same exit codes, zero workflow commands.
      const localFlaky = runFixtureWithRealConfig(configCase, flakySpec, { CI: "true" });
      expect(localFlaky.status, localFlaky.stdout + localFlaky.stderr).toBe(0);
      expect(localFlaky.stdout).toContain("Running 1 test");
      expect(localFlaky.stdout).not.toMatch(/^::(?:error|warning|notice)\b/m);
      expect(existsSync(localFlaky.reportIndexPath)).toBe(true);

      const localFail = runFixtureWithRealConfig(configCase, failingSpec, { CI: "true" });
      expect(localFail.status, localFail.stdout + localFail.stderr).toBe(1);
      expect(localFail.stdout).toContain("Running 1 test");
      expect(localFail.stdout).not.toMatch(/^::(?:error|warning|notice)\b/m);
      expect(existsSync(localFail.reportIndexPath)).toBe(true);
    }, 120_000);
  }

  it("CI sandbox shard runner preserves config reporters on execution shards and isolates --list", () => {
    const script = readFileSync(join(ANTEROOM_DIR, "scripts", "check-browser-shards.sh"), "utf8");
    expect(script).toMatch(/npm run e2e -- --list --reporter="\$reporter"/);
    expect(script).toMatch(/npm run e2e -- --shard="\$shard\/2" --output="\$output\/results-\$shard"/);
    expect(script).not.toMatch(/--shard="\$shard\/2"[^\n]*--reporter/);

    const ciWorkflow = readFileSync(join(REPO_ROOT, ".github", "workflows", "ci.yml"), "utf8");
    expect(ciWorkflow).toContain('bash scripts/check-browser-shards.sh "$RUNNER_TEMP/sandbox-browser"');
    expect(ciWorkflow).toContain("npm run e2e:hosted");
  });
});
