"""安全な使い捨てDBで検証し、完全ログとは別に短い結果だけを返す。"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verification import ROOT, fingerprint, safe_environment, select, summarize


def read_report(path: Path) -> dict:
    """壊れた、または途中の記録をキャッシュ成功として扱わない。"""
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def interrupted(signum, frame):
    """CIの中断でも子プロセスと専用DBを回収する。"""
    raise KeyboardInterrupt


def main() -> int:
    """テスト選択・実行・終了判定・後片付け。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=["full", "fast", "changed"], default="changed"
    )
    parser.add_argument("--base")
    parser.add_argument("--shard")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    if args.shard:
        try:
            index, total = map(int, args.shard.split("/"))
            valid = 1 <= index <= total <= 8
        except ValueError:
            valid = False
        if not valid:
            parser.error("shard must be index/total, total <= 8")
    signal.signal(signal.SIGTERM, interrupted)
    targets, reason = select(args.profile, args.base)
    scope = {
        "profile": args.profile,
        "reason": reason,
        "targets": targets,
        "tests": args.tests,
        "shard": args.shard,
        "environment": "disposable-v1",
    }
    if args.plan:
        print(json.dumps(scope))
        return 0
    os.umask(0o077)
    root = ROOT / ".verify"
    root.mkdir(exist_ok=True)
    key = fingerprint(scope)
    if args.reuse and not os.environ.get("CI"):
        for previous in sorted(root.glob("*/result.json"), reverse=True):
            row = read_report(previous)
            if (
                row.get("fingerprint") == key
                and row.get("status") == "passed"
                and row.get("complete")
                and 0 <= time.time() - previous.stat().st_mtime < 3600
            ):
                print(
                    json.dumps(
                        {
                            "status": "reused",
                            "run": str(previous.parent),
                            "counts": row["counts"],
                        }
                    )
                )
                return 0
    run = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    run.mkdir()
    compose = run / "compose.yml"
    compose.write_text(
        "services:\n  postgres_test:\n    image: postgres:16-alpine\n"
        "    environment:\n      POSTGRES_DB: syncnesto_test\n"
        "      POSTGRES_USER: admin\n      POSTGRES_PASSWORD: verification-only\n"
        "    ports:\n      - '127.0.0.1::5432'\n"
        "    tmpfs:\n      - /var/lib/postgresql/data\n"
    )
    env = safe_environment()
    env.update(
        {
            "COMPOSE_FILE": str(compose),
            "COMPOSE_PROJECT_NAME": "verify-" + uuid4().hex[:12],
            "DATABASE_URL": "postgresql://admin:verification-only@127.0.0.1:0/syncnesto_test",
        }
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.pytest_reporter",
        "-q",
        "--tb=short",
        "--durations=10",
        "--junitxml=" + str(run / "junit.xml"),
        "--verification-report=" + str(run / "report.json"),
    ]
    if args.profile == "fast":
        command += ["-m", "no_db"]
    if targets:
        for target in targets:
            command += ["--verification-select", target]
    if args.shard:
        command += ["--verification-shard", args.shard]
    command += args.tests
    started = time.perf_counter()
    timed_out = False
    cleanup_failed = False
    exit_code = 2
    with (run / "full.log").open("w") as log:
        try:
            if args.profile != "fast":
                subprocess.run(
                    ["docker", "compose", "up", "-d", "postgres_test"],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=90,
                )
                port = (
                    subprocess.check_output(
                        ["docker", "compose", "port", "postgres_test", "5432"],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        timeout=30,
                    )
                    .strip()
                    .rsplit(":", 1)[1]
                )
                assert port.isdigit() and int(port) > 0
                env["DATABASE_URL"] = (
                    f"postgresql://admin:verification-only@127.0.0.1:{port}/syncnesto_test"
                )
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                exit_code = 124
        except KeyboardInterrupt:
            timed_out = True
            exit_code = 130
            log.write("verification interrupted\n")
        except (subprocess.SubprocessError, OSError, AssertionError) as error:
            log.write(str(error) + "\n")
        finally:
            if args.profile != "fast":
                try:
                    cleanup = subprocess.run(
                        ["docker", "compose", "down"],
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=log,
                        timeout=60,
                        check=False,
                    )
                    cleanup_failed = cleanup.returncode != 0
                except (subprocess.SubprocessError, OSError) as error:
                    log.write("cleanup failed: " + str(error) + "\n")
                    cleanup_failed = True
    report = read_report(run / "report.json")
    if cleanup_failed:
        exit_code = 2
    result = summarize(report, exit_code, timed_out)
    result.update(
        {
            "fingerprint": key,
            "scope": scope,
            "seconds": round(time.perf_counter() - started, 3),
            "run": str(run),
            "cleanup_failed": cleanup_failed,
        }
    )
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    brief = {
        k: result[k]
        for k in ["status", "complete", "counts", "seconds", "phase_seconds", "run"]
    }
    print(json.dumps(brief, ensure_ascii=False))
    if result["status"] != "passed":
        for failure in result["failures"][:3]:
            print(
                failure["id"]
                + " ["
                + failure["phase"]
                + "]\n"
                + failure["error"][-1800:]
            )
        if not result["failures"]:
            print((run / "full.log").read_text(errors="replace")[-3500:])
        print("全失敗・収集/起動エラー・完全ログ: " + str(run / "full.log"))
    return 0 if result["status"] == "passed" else (exit_code if exit_code > 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
