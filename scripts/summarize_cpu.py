"""cpu_diagnostic JSONLを、機密値を展開せずmethod/toolごとに集計する。"""

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def summarize(rows: list[dict]) -> list[dict]:
    """重複IDを除き、重なったプロセスCPUを足し合わせない。"""
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        key = row.get("diagnostic_id")
        if row.get("event") != "cpu_diagnostic" or not key or key in seen:
            continue
        seen.add(key)
        groups[(row["endpoint"], row.get("mcp_method"), row.get("mcp_tool"))].append(
            row
        )
    output = []
    for (endpoint, method, tool), samples in groups.items():
        walls = sorted(r["wall_ms"] for r in samples)
        serial = [r for r in samples if not r["overlap"]]
        output.append(
            {
                "endpoint": endpoint,
                "mcp_method": method,
                "mcp_tool": tool,
                "calls": len(samples),
                "wall_total_ms": sum(walls),
                "wall_mean_ms": statistics.mean(walls),
                "wall_p95_ms": walls[math.ceil(len(walls) * 0.95) - 1],
                "serial_cpu_samples": len(serial),
                "overlap_samples": len(samples) - len(serial),
                "process_cpu_total_ms_serial_only": sum(
                    r["process_cpu_ms"] for r in serial
                ),
                "process_cpu_mean_ms_serial_only": statistics.mean(
                    r["process_cpu_ms"] for r in serial
                )
                if serial
                else "未計測",
                "db_queries": sum(r["db_queries"] for r in samples),
                "db_execute_total_ms": sum(r["db_execute_ms"] for r in samples),
                "response_mean_bytes": statistics.mean(
                    r["response_bytes"] for r in samples
                ),
                "response_max_bytes": max(r["response_bytes"] for r in samples),
                "serialization_scope": "mcp_text_content_only",
                "serialization_mean_ms": statistics.mean(
                    r["serialization_ms"]
                    for r in samples
                    if r["serialization_ms"] is not None
                )
                if any(r["serialization_ms"] is not None for r in samples)
                else "未計測",
            }
        )
    return sorted(
        output, key=lambda r: r["process_cpu_total_ms_serial_only"], reverse=True
    )


def main():
    """生ログへ戻らず集計JSONを出力する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    rows = []
    for line in args.input.read_text().splitlines():
        try:
            row = json.loads(line)
            if isinstance(row.get("message"), str):
                row = json.loads(row["message"])
            if isinstance(row, dict):
                rows.append(row)
        except (ValueError, AttributeError):
            continue
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
