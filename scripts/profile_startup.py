"""通常DB/envを継承せず、新Pythonプロセスの起動CPUを測る手動診断。"""

import argparse
import cProfile
import json
import os
import pstats
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns, process_time_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verification import ROOT, safe_environment


def worker():
    """import・S3 client生成・追加create_appを分けて数値だけ返す。"""
    profile = cProfile.Profile(timer=process_time_ns, timeunit=1e-9)
    wall, cpu = perf_counter_ns(), process_time_ns()
    clients = []
    profiled = os.environ.get("PROFILE_STARTUP") == "1"
    if profiled:
        profile.enable()
    import boto3

    original = boto3.client

    def client(*args, **kwargs):
        started = process_time_ns()
        try:
            return original(*args, **kwargs)
        finally:
            clients.append((process_time_ns() - started) / 1e6)

    boto3.client = client
    from app.main import app, create_app

    profile.disable()
    imported = {
        "wall_ms": (perf_counter_ns() - wall) / 1e6,
        "cpu_ms": (process_time_ns() - cpu) / 1e6,
        "s3_clients": len(clients),
        "s3_client_cpu_ms": sum(clients),
    }
    top = (
        sorted(
            getattr(pstats.Stats(profile), "stats").items(),
            key=lambda item: item[1][3],
            reverse=True,
        )[:40]
        if profiled
        else []
    )
    cpu, wall = process_time_ns(), perf_counter_ns()
    create_app()
    return {
        "import_app": imported,
        "recreate_warm_app": {
            "cpu_ms": (process_time_ns() - cpu) / 1e6,
            "wall_ms": (perf_counter_ns() - wall) / 1e6,
        },
        "mcp_enabled": os.environ["MCP_ENABLED"],
        "profiled": profiled,
        "routes": len(app.routes),
        "profile_top_cumulative": [
            {
                "file": Path(key[0]).name,
                "line": key[1],
                "function": key[2],
                "calls": val[1],
                "self_cpu_ms": val[2] * 1000,
                "cumulative_cpu_ms": val[3] * 1000,
            }
            for key, val in top
        ],
    }


def main():
    """各5標本の無profile計測と、別標本でのCPU profileを保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker()))
        return
    rows = []
    for enabled in (False, True):
        for index in range(args.samples + 1):
            env = safe_environment()
            env.update(
                DATABASE_URL="postgresql://test:test@127.0.0.1:59996/syncnesto_test",
                MCP_ENABLED=str(enabled).lower(),
                MCP_ISSUER_URL="http://127.0.0.1:8000",
                MCP_RESOURCE_URL="http://127.0.0.1:8000/mcp",
                LOG_LEVEL="ERROR",
                PROFILE_STARTUP="1" if index == args.samples else "0",
            )
            output = subprocess.check_output(
                [sys.executable, __file__, "--worker"],
                env=env,
                text=True,
                cwd=ROOT,
                timeout=90,
            )
            rows.append(json.loads(output.strip().splitlines()[-1]))
    destination = ROOT / ".verify/mcp-cpu-20261105"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "startup.json").write_text(json.dumps(rows, indent=2))
    print(
        json.dumps({"samples": len(rows), "result": str(destination / "startup.json")})
    )


if __name__ == "__main__":
    main()
