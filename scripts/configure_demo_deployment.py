"""Vercelの取得済みAPP_ENVに対応する公開設定だけを生成する。"""

import json
from pathlib import Path

from dotenv import dotenv_values


def deployment_config(config: dict, app_env: str) -> dict:
    """通常環境にはCronを追加せず、デモだけ日次回収を登録する。"""
    if app_env not in {"production", "demo"}:
        raise ValueError("Vercel deployment requires APP_ENV=production or demo")
    result = dict(config)
    if app_env == "demo":
        result["crons"] = [{"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"}]
    return result


def main() -> None:
    """取得済み秘密を出力・設定JSONへコピーしない。"""
    root = Path(__file__).resolve().parents[1]
    pulled = dotenv_values(root / ".vercel/.env.production.local")
    config = deployment_config(
        json.loads((root / "vercel.json").read_text()), pulled.get("APP_ENV") or ""
    )
    (root / ".vercel/deploy-config.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
