"""Export only concise synthetic results; private page/account data stays local."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/EVALUATION-RESULTS.md")
    )
    args = parser.parse_args()
    records = [
        json.loads(path.read_text())
        for path in (args.artifacts / "evals").glob("simple-*/*.json")
    ]
    records = sorted(
        (r for r in records if r.get("format") == "simple-eval-v1"),
        key=lambda r: r["started_at"],
    )
    lines = [
        "# Synthetic evaluation attempts",
        "",
        "Every retained simplified-harness attempt is included. These are fixture results, not a live-site reliability estimate. Letter semantics require the optional native judge or manual review.",
        "",
        "| Run | Case | State checks | Letter review | USD | LangSmith |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for record in records:
        lines.append(
            f"| `{record['run_id']}` | {record['case']} | {'PASS' if record['passed'] else 'FAIL'} | {record['letter_review']['status']} | {record['cost_usd']:.6f} | {record.get('langsmith', {}).get('status', 'not requested')} |"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")
    print(f"Exported {len(records)} attempts to {args.output}")


if __name__ == "__main__":
    main()
