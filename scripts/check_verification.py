"""全CI分割の成功と、未実行・重複ケースがないことを検証する。"""

import argparse
import json
from pathlib import Path


def check(root: Path, expected: int) -> int:
    """部分成功だけで全体成功と判定しない。"""
    results = list(root.rglob("result.json"))
    if len(results) != expected:
        raise ValueError("required shard results are missing or duplicated")
    universe = None
    covered = set()
    scopes = set()
    for path in results:
        result = json.loads(path.read_text())
        report = json.loads(path.with_name("report.json").read_text())
        if result["status"] != "passed" or not result["complete"]:
            raise ValueError("a shard failed or did not complete")
        shard = result["scope"]["shard"]
        if shard in scopes or shard not in {
            f"{n}/{expected}" for n in range(1, expected + 1)
        }:
            raise ValueError("invalid shard identity")
        scopes.add(shard)
        current = set(report["all_collected"])
        if universe is not None and current != universe:
            raise ValueError("shards collected different test versions")
        universe = current
        selected = set(report["selected"])
        if covered & selected:
            raise ValueError("a test ran in more than one shard")
        covered |= selected
    if not universe or covered != universe:
        raise ValueError("some collected tests did not run")
    return len(covered)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--shards", type=int, default=2)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "status": "passed",
                "complete": True,
                "tests": check(args.root, args.shards),
            }
        )
    )
