import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { relative } from "node:path";

const hash = (value) => createHash("sha256").update(value).digest("hex");

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
      const cases = this.tests.map((test) => ({
        id: hash(test.id),
        project: test.parent.project().name,
        file: hash(relative(this.root, test.location.file).split("\\").join("/")),
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
