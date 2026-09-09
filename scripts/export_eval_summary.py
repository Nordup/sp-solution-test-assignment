"""Export selected synthetic evaluation metadata, never raw account observations."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-session", default="final-candidate")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--output", type=Path, default=Path("docs/EVALUATION-RESULTS.md"))
    args = parser.parse_args()
    records = []
    for path in (args.artifacts / "evals").glob("*/case-*.json"):
        record = json.loads(path.read_text())
        if record.get("release_session") == args.release_session:
            records.append(record)
    records.sort(key=lambda item: item["started_at"])
    lines = [
        "# Synthetic evaluation attempts",
        "",
        "Generated from retained local case reports. Every attempt is included; an overall failure remains a failure even when browser-state checks succeed. Different runtime fingerprints are different implementation versions, so these attempts are not a controlled reliability estimate. Real-account observations and credentials are excluded.",
        "",
        "See [VALIDATION.md](VALIDATION.md) for release status and [FINAL-TEST.md](FINAL-TEST.md) for required gates. Trace verification means the actual root and nested events were retrieved from LangSmith; it does not mean the task passed. Full traces remain in the configured private LangSmith workspace.",
        "",
        "| Run ID | Case / seed | Runtime | Overall | Checks passed | Trace verified | Settled USD | Unknown USD |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for record in records:
        checks = record.get("grade", {}).get("checks", {})
        budget = record.get("budget", {})
        passed = record.get("passed") and record.get("langsmith_verified")
        lines.append(
            f"| `{record['run_id']}` | `{record['case']}` / {record['seed']} "
            f"| `{record['runtime_fingerprint'][:12]}` | {'PASS' if passed else 'FAIL'} "
            f"| {sum(value is True for value in checks.values())}/{len(checks)} "
            f"| {'yes' if record.get('langsmith_verified') else 'no'} "
            f"| {budget.get('settled', 0) / 1_000_000:.6f} "
            f"| {budget.get('unknown', 0) / 1_000_000:.6f} |"
        )
    lines += [
        "",
        "Unknown amounts are retained generation reservations, not confirmed charges or refunds. Aggregate release holds may be larger. Setup/provider preflight costs are recorded separately in the release ledger and preflight reports; this table covers task attempts only.",
        "",
        "Regenerate with `uv run python scripts/export_eval_summary.py --release-session final-candidate`.",
    ]
    args.output.write_text("\n".join(lines) + "\n")
    print(f"Exported {len(records)} attempts to {args.output}")


if __name__ == "__main__":
    main()
