import { createHmac } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";

export default class BrowserEvidenceReporter {
  printsToStdio() {
    return false;
  }

  onBegin(config, suite) {
    this.root = config.rootDir;
    this.tests = suite.allTests();
  }

  onError() {
    this.hadError = true;
  }

  onEnd(result) {
    const output = process.env.SANDBOX_BROWSER_EVIDENCE_FILE;
    if (!output || result.status !== "passed" || this.hadError || !this.tests?.length) return;

    try {
      const key = readFileSync(join(dirname(output), "identity-key"));
      if (key.length !== 32) throw new Error("Invalid evidence key");
      const hash = (kind, value) => createHmac("sha256", key).update(kind + "\0" + value).digest("hex");
      const cases = this.tests.map((test) => ({
        id: hash("case", test.id),
        project: test.parent.project().name,
        file: hash("file", relative(this.root, test.location.file).split("\\").join("/")),
        expected: test.expectedStatus,
        attempts: test.results.map(({ status, duration, retry }) => ({ status, duration, retry })),
      }));
      writeFileSync(output, JSON.stringify(cases), { flag: "wx" });
    } catch {
      // Coverage still comes from the ordinary Playwright run. The evidence
      // validator rejects an absent or incomplete observation separately.
      console.warn("Could not record sandbox browser evidence.");
    }
  }
}
