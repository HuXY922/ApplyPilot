#!/usr/bin/env python3
"""Explicit low-volume live source smoke, separate from deterministic tests.

No profiles, model calls, credentials, application submission or database writes.
Prints compact provenance/coverage, never full employer JD bodies. Calls ordinary
reviewed Transport paths with the same TLS/host/redirect/budget restrictions.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applypilot_core import runtime, official_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=tuple(official_sources.BEISEN_SOURCES), default="cxmt")
    parser.add_argument("--query", default="AI")
    parser.add_argument("--max-requests", type=int, choices=range(2, 7), default=3)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = runtime.discover(args.query, source_config={"sources": [args.source], "max_requests": args.max_requests})
    report = {"kind": "live_official_source_read", "checked_at": runtime._now(), "source": args.source,
              "source_summary": result["source_summary"], "failures": result["failures"],
              "jobs": [{"id": row["id"], "title": row["title"], "canonical_url": row["canonical_url"],
                        "availability": row["availability"], "recruitment_type": row["recruitment_type"],
                        "method": row["verification"].get("method"), "evidence_hash": row["verification"]["evidence_hash"]}
                       for row in result["jobs"]]}
    report["source_read_succeeded"] = bool(report["source_summary"]) and not report["failures"] and all(item.get("pages", 0) > 0 for item in report["source_summary"])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n")
    return 0 if report["source_read_succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
