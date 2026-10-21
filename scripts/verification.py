"""依存追加なしの選択・指紋・結果集計。アプリやDBへは接続しない。"""

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = {
    "task": ["test_tasks", "test_requirements", "test_home", "test_notifications"],
    "requirement": [
        "test_requirements",
        "test_tasks",
        "test_fixed_approvals",
        "test_home",
    ],
    "notification": [
        "test_notifications",
        "test_fixed_approval_notifications",
        "test_tenants",
    ],
    "test_design": [
        "test_test_designs",
        "test_test_collaboration",
        "test_artifact_test_design",
    ],
    "specification": [
        "test_specifications",
        "test_specification_lifecycle",
        "test_requirements",
    ],
    "audit_log": ["test_audit_logs", "test_audit_log", "test_audit_log_view"],
}


def git(*args: str) -> str:
    """引数をshellへ渡さずGitを読む。"""
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_paths(base: str) -> list[str]:
    """実際のbaseとのmerge-base、未commit、追加、削除、renameを含める。"""
    ancestor = git("merge-base", "HEAD", base)
    paths = set()
    for args in [
        ("diff", "--name-only", "--no-renames", "-z", ancestor),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ]:
        paths.update(git(*args).split("\0"))
    return sorted(p for p in paths if p)


def select(profile: str, base: str | None) -> tuple[list[str], str]:
    """未対応・共通変更は必ず全体へ戻す。空集合を成功にしない。"""
    if profile != "changed":
        return [], profile
    if not base:
        return [], "full: base unspecified"
    try:
        paths = changed_paths(base)
    except subprocess.CalledProcessError:
        return [], "full: base unavailable"
    selected = set()
    for path in paths:
        if (
            path.startswith("tests/")
            and path.endswith(".py")
            and Path(path).name.startswith("test_")
        ):
            if not (ROOT / path).exists():
                return [], "full: deleted test"
            selected.add(path)
            continue
        if path.startswith(("docs/",)) or path.endswith(".md"):
            continue
        if not path.startswith(("app/services/", "app/routers/")):
            return [], "full: common or unmapped change"
        stem = Path(path).stem.removesuffix("s")
        domain = next(
            (key for key in DOMAINS if stem == key or stem.startswith(key + "_")), None
        )
        if domain is None:
            return [], "full: unmapped business change"
        for name in DOMAINS[domain]:
            selected.update(
                str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob(name + ".py")
            )
    return sorted(selected), "related + no_db" if selected else "full: empty mapping"


def fingerprint(scope: dict) -> str:
    """未commit内容・lock・runtime・テスト環境を含め、秘密値は出力しない。"""
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode())
    digest.update(sys.version.encode())
    digest.update(
        json.dumps(
            sorted((d.metadata["Name"], d.version) for d in distributions())
        ).encode()
    )
    for name in git("ls-files", "-co", "--exclude-standard", "-z").split("\0"):
        if not name or name.startswith(".verify/"):
            continue
        path = ROOT / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"deleted")
    return digest.hexdigest()


def summarize(report: dict, exit_code: int, timed_out: bool = False) -> dict:
    """pytest以外のエラーもfail-closedにする。"""
    reports = report.get("reports", [])
    selected = report.get("selected", [])
    phases = {name: {} for name in selected}
    duplicate = False
    for row in reports:
        if row["id"] not in phases or row["phase"] in phases[row["id"]]:
            duplicate = True
            continue
        phases[row["id"]][row["phase"]] = row["outcome"]
    complete = bool(report.get("finished") and selected and not duplicate)
    for phase in phases.values():
        complete = complete and "setup" in phase and "teardown" in phase
        if phase.get("setup") == "passed":
            complete = complete and "call" in phase
    counts = Counter()
    for row in reports:
        if row["outcome"] == "failed":
            counts["failed"] += 1
        elif row["phase"] == "call" or (
            row["phase"] == "setup" and row["outcome"] == "skipped"
        ):
            outcome = row["outcome"]
            if row.get("xfail"):
                outcome = "xpassed" if outcome == "passed" else "xfail"
            counts[outcome] += 1
    timings = {
        phase: round(sum(r["seconds"] for r in reports if r["phase"] == phase), 3)
        for phase in ["setup", "call", "teardown"]
    }
    return {
        "status": "passed"
        if exit_code == 0 and complete and not timed_out and not counts["failed"]
        else "failed",
        "exit_code": exit_code,
        "complete": complete,
        "timed_out": timed_out,
        "collected": len(report.get("all_collected", [])),
        "selected": len(selected),
        "counts": dict(counts),
        "phase_seconds": timings,
        "failures": [r for r in reports if r["outcome"] == "failed"],
    }


def safe_environment() -> dict[str, str]:
    """既存のdotenv/クラウド/SMTP資格を継承しない。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "VIRTUAL_ENV", "SYSTEMROOT"}
    }
    env.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "SYNCNESTO_VERIFY": "1",
            "APP_ENV": "test",
            "SQL_ECHO": "false",
            "LOG_FILE": "",
            "SECRET_KEY": "verification-dummy-key-not-for-production",
            "MFA_REQUIRED": "false",
            "EMAIL_PROVIDER": "disabled",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": "ap-northeast-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_S3_BUCKET_NAME": "verification-unused",
            "AWS_S3_ENDPOINT_URL": "http://127.0.0.1:59997",
        }
    )
    return env
