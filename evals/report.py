"""Fail-closed evidence aggregation; this command never runs an evaluation."""

import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from browser_agent.config import Settings

REQUIRED_IDS = {
    f"{prefix}{i:02d}"
    for prefix, count in [("P", 14), ("B", 13), ("F", 20)]
    for i in range(1, count + 1)
}
REQUIRED_CASES = {
    "mail_latest_10",
    "food_previous_order",
    "jobs_resume_3",
    "unfamiliar_event",
    "food_layout_variant",
    "stale_ref_recovery",
    "consequential_denied",
}
MODEL_FAILURE_COVERAGE = {
    "F15": {"food_history_ambiguous", "food_item_unavailable"},
    "F16": {"mail_classification_ambiguous"},
    "F17": {"jobs_already_applied", "jobs_unsupported_qualifications"},
}


def runtime_fingerprint(root=Path(".")):
    digest = hashlib.sha256()
    files = sorted((root / "src").rglob("*.py")) + sorted((root / "evals").glob("*.py"))
    files += [root / "pyproject.toml", root / "uv.lock"]
    for path in files:
        if path.is_file():
            digest.update(
                str(path.relative_to(root)).encode() + b"\0" + path.read_bytes()
            )
    return digest.hexdigest()


def git_sha():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip()


def preflight_fingerprint(root=Path(".")):
    """Only dependencies exercised by the provider/budget/protocol smoke."""
    digest = hashlib.sha256()
    files = [
        root / "src/browser_agent" / name
        for name in ("config.py", "llm.py", "tools.py", "storage.py")
    ]
    files += [root / "pyproject.toml", root / "uv.lock"]
    for path in files:
        if path.is_file():
            digest.update(
                str(path.relative_to(root)).encode() + b"\0" + path.read_bytes()
            )
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    path.chmod(0o600)


def junit_result(path):
    path = Path(path)
    if not path.is_file():
        return {"passed": False, "reason": "missing JUnit", "tests": [], "ids": []}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {"passed": False, "reason": "invalid JUnit", "tests": [], "ids": []}
    cases = list(root.iter("testcase"))
    names = [case.attrib.get("name", "") for case in cases]
    ids = set()
    for case in cases:
        name = case.attrib.get("name", "")
        ids.update(
            m.upper()
            for m in re.findall(r"(?:^|_)([pbf]\d{2})(?=_|\[|$)", name, re.IGNORECASE)
        )
        for prop in case.findall("./properties/property"):
            if prop.attrib.get("name") == "acceptance_ids":
                ids.update(prop.attrib.get("value", "").split(","))
    bad = [
        case.attrib.get("name", "")
        for case in cases
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
    ]
    return {
        "passed": bool(cases) and not bad,
        "tests": names,
        "ids": sorted(ids),
        "failed_or_skipped": bad,
        "mtime": path.stat().st_mtime,
    }


def require_deterministic(settings):
    """Paid stages require completed nonempty zero-skip safety/browser reports."""
    results = {
        name: junit_result(settings.artifact_dir / "final" / name)
        for name in ("03-contracts.xml", "04-browser.xml")
    }
    latest_code = max(
        (p.stat().st_mtime for p in Path("src/browser_agent").glob("*.py")), default=0
    )
    failed = [
        name
        for name, result in results.items()
        if not result["passed"] or result.get("mtime", 0) < latest_code
    ]
    if failed:
        raise RuntimeError(
            "Paid evaluation gated: run current deterministic stages first: "
            + ", ".join(failed)
        )
    return results


def manual_check(entry, fingerprint):
    """An existing artifact alone is never evidence of a successful human review."""
    if not isinstance(entry, dict):
        return False
    if entry.get("status") != "PASS" or not all(
        entry.get(k)
        for k in (
            "reviewer",
            "reviewed_at",
            "description",
            "evidence_path",
            "evidence_sha256",
        )
    ):
        return False
    try:
        if datetime.fromisoformat(entry["reviewed_at"]).tzinfo is None:
            return False
    except (ValueError, TypeError):
        return False
    path = Path(entry["evidence_path"])
    return bool(
        entry.get("runtime_fingerprint") == fingerprint
        and path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == entry["evidence_sha256"]
    )


def ordered_stages(stages, preflight, latest, manual):
    """Reject missing/misordered evidence instead of inferring intended order."""
    try:
        times = [
            stages["03-contracts.xml"]["mtime"],
            stages["04-browser.xml"]["mtime"],
            datetime.fromisoformat(preflight["started_at"]).timestamp(),
        ]
        times += [
            datetime.fromisoformat(latest[case]["started_at"]).timestamp()
            for case in (
                "mail_latest_10",
                "food_previous_order",
                "jobs_resume_3",
                "unfamiliar_event",
                "food_layout_variant",
                "stale_ref_recovery",
                "consequential_denied",
            )
        ]
        times += [
            stages["08-failures.xml"]["mtime"],
            datetime.fromisoformat(manual["live_video"]["reviewed_at"]).timestamp(),
            datetime.fromisoformat(
                manual["repository_audit"]["reviewed_at"]
            ).timestamp(),
        ]
    except (KeyError, ValueError, TypeError):
        return False
    return times == sorted(times)


def build_report(settings, session, root=Path(".")):
    fingerprint = runtime_fingerprint(root)
    base = settings.artifact_dir / "final"
    stages = {
        name: junit_result(base / name)
        for name in ("03-contracts.xml", "04-browser.xml", "08-failures.xml")
    }
    manual_path = base / "sessions" / session / "manual.json"
    manual = json.loads(manual_path.read_text()) if manual_path.exists() else {}
    ids = {i for stage in stages.values() for i in stage["ids"]}
    tests = {
        name for stage in stages.values() if stage["passed"] for name in stage["tests"]
    }
    # Optional explicit mappings must name actual executed tests, not assertion-free IDs.
    for test_id, names in manual.get("test_id_mapping", {}).items():
        if (
            test_id in REQUIRED_IDS
            and isinstance(names, list)
            and names
            and set(names) <= tests
        ):
            ids.add(test_id)
    records = []
    for path in sorted((settings.artifact_dir / "evals").glob("*/case-*.json")):
        record = json.loads(path.read_text())
        if record.get("release_session") == session:
            records.append(record | {"report_path": str(path)})
    current = [
        record
        for record in records
        if record.get("runtime_fingerprint") == fingerprint
        and record.get("model") == settings.model
    ]
    latest = {}
    for record in sorted(current, key=lambda item: item.get("started_at", "")):
        latest[record["case"]] = record
    model_failure_coverage = {}
    for requirement, cases in MODEL_FAILURE_COVERAGE.items():
        model_failure_coverage[requirement] = all(
            latest.get(case, {}).get("passed") is True
            and latest.get(case, {}).get("langsmith_verified") is True
            and latest.get(case, {})
            .get("grade", {})
            .get("quality_review", {})
            .get("grounded")
            is True
            for case in cases
        )
        if model_failure_coverage[requirement]:
            ids.add(requirement)
    missing_cases = sorted(REQUIRED_CASES - latest.keys())
    failed_cases = sorted(
        case
        for case, record in latest.items()
        if not record.get("passed") or not record.get("langsmith_verified")
    )
    preflight_path = base / "sessions" / session / "preflight.json"
    preflight = (
        json.loads(preflight_path.read_text()) if preflight_path.exists() else {}
    )
    manual_status = {
        key: manual_check(manual.get(key), fingerprint)
        for key in ("setup", "live_video", "repository_audit")
    }
    latest_source = max(
        (p.stat().st_mtime for p in (root / "src").rglob("*.py")), default=0
    )
    stale_stages = [
        name for name, value in stages.items() if value.get("mtime", 0) < latest_source
    ]
    missing_ids = sorted(REQUIRED_IDS - ids)
    order_passed = ordered_stages(stages, preflight, latest, manual)
    all_pass = (
        all(s["passed"] for s in stages.values())
        and not stale_stages
        and not missing_ids
        and not missing_cases
        and not failed_cases
        and all(manual_status.values())
        and preflight.get("passed") is True
        and preflight.get("protocol_fingerprint") == preflight_fingerprint(root)
        and preflight.get("model") == settings.model
        and order_passed
    )
    return {
        "overall": "PASS" if all_pass else "NOT READY",
        "release_session": session,
        "runtime_fingerprint": fingerprint,
        "stages": stages,
        "stale_stages": stale_stages,
        "required_stage_order_passed": order_passed,
        "missing_test_ids": missing_ids,
        "missing_cases": missing_cases,
        "failed_cases": failed_cases,
        "manual_reviews": manual_status,
        "model_failure_coverage": model_failure_coverage,
        "preflight": preflight,
        "attempt_count": len(records),
        "attempts": records,
        "limitations": [
            "Test-ID coverage is necessary but not sufficient: reviewers must inspect assertion scope.",
            "Fixture results do not prove live-site compatibility.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-session", required=True)
    parser.add_argument("--require-final-suite", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings.load()
    report = build_report(settings, args.release_session)
    path = (
        settings.artifact_dir
        / "final"
        / "sessions"
        / args.release_session
        / "report.json"
    )
    write_json(path, report)
    print(json.dumps({k: v for k, v in report.items() if k != "attempts"}, indent=2))
    print(f"Full evidence report: {path}")
    if args.require_final_suite and report["overall"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
