#!/usr/bin/env python3
"""Score measure-this against measurements the seller typed by hand.

The eval set lives outside the repo: listing photos never get committed. A manifest
lists each ruler photo, the garment type, the measurements to read, and the truth:

    {"cases": [{"id": "shirt-1", "photos": ["/path/IMG_1.jpg"], "garment": "top",
                "measures": ["pit_to_pit", "length"],
                "truth": {"pit_to_pit": 20.5, "length": 27}}]}

A case may have several ruler photos (pants usually take two); each is cut into its
own numbered folder and the reader decides which photo carries which measurement.

Cases with no truth are read but not scored. Two independent reads come back as JSON,
each {"<case id>": {"<measure>": 20.5 | "cannot read"}}:

    python3 eval.py crops --manifest manifest.json --out crops/
    python3 eval.py score --manifest manifest.json --reads first.json second.json

"Confidently wrong" is the number that matters: a value the skill would have offered
the seller that is more than half an inch off.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ruler import crop, parse_inches, settle_one


def make_crops(manifest: dict, out: Path) -> None:
    for case in manifest["cases"]:
        for number, photo in enumerate(case["photos"], 1):
            report = crop(Path(photo), out / case["id"] / str(number), [], set())
            print(case["id"], number, [(stick["stick"], len(stick["tiles"])) for stick in report["sticks"]])


def score(manifest: dict, first: dict, second: dict) -> dict:
    rows = []
    for case in manifest["cases"]:
        for measure in case["measures"]:
            reads = (first.get(case["id"], {}).get(measure), second.get(case["id"], {}).get(measure))
            settled = settle_one(*reads)
            truth = case["truth"].get(measure)
            row = {"case": case["id"], "measure": measure, "truth": truth, **settled}
            if truth is not None and settled["status"] == "accepted":
                row["error"] = round(settled["value"] - truth, 3)
            rows.append(row)

    scored = [row for row in rows if row["truth"] is not None]
    total = len(scored)

    def share(test) -> str:
        count = sum(1 for row in scored if test(row))
        return f"{count}/{total} ({count / total:.0%})" if total else "0/0"

    accepted = lambda row: row["status"] == "accepted"  # noqa: E731
    single = [
        abs(parse_inches(read) - row["truth"])
        for row in scored
        for read in row["reads"]
        if parse_inches(read) is not None
    ]
    summary = {
        "scored": total,
        "within_quarter": share(lambda r: accepted(r) and abs(r["error"]) <= 0.25),
        "within_half": share(lambda r: accepted(r) and abs(r["error"]) <= 0.5),
        "refused": share(lambda r: not accepted(r)),
        "confidently_wrong": share(lambda r: accepted(r) and abs(r["error"]) > 0.5),
        "single_reads_within_half": f"{sum(e <= 0.5 for e in single)}/{len(single)}",
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    cropper = commands.add_parser("crops", help="cut crops for every photo in the manifest")
    cropper.add_argument("--manifest", type=Path, required=True)
    cropper.add_argument("--out", type=Path, required=True)
    scorer = commands.add_parser("score", help="score two independent reads against the truth")
    scorer.add_argument("--manifest", type=Path, required=True)
    scorer.add_argument("--reads", type=Path, nargs=2, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    if args.command == "crops":
        make_crops(manifest, args.out)
    else:
        first, second = (json.loads(path.read_text()) for path in args.reads)
        print(json.dumps(score(manifest, first, second), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
