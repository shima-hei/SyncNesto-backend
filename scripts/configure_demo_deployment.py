"""Vercelの取得済みDEMO_MODEに対応する公開設定だけを生成する。"""

import json
from pathlib import Path

from dotenv import dotenv_values


def deployment_config(
    config: dict,
    app_env: str,
    cleanup_mode: str = "disabled",
    *,
    demo_mode: bool = False,
    demo_cleanup_configured: bool = False,
) -> dict:
    """環境ごとに明示した回収だけを日次登録し、秘密を含めない。"""
    if app_env != "production":
        raise ValueError("Vercel deployment requires APP_ENV=production")
    result = dict(config)
    if cleanup_mode not in {"disabled", "dry_run", "execute"}:
        raise ValueError("Unknown DELETED_DATA_CLEANUP_MODE")
    # 前の環境向けCronを持ち込まない。
    result.pop("crons", None)
    crons = []
    if demo_mode or demo_cleanup_configured:
        crons.append({"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"})
    if cleanup_mode != "disabled":
        crons.append({"path": "/internal/trash/cleanup", "schedule": "0 19 * * *"})
    if crons:
        result["crons"] = crons
    return result


def main() -> None:
    """取得済み秘密を出力・設定JSONへコピーしない。"""
    root = Path(__file__).resolve().parents[1]
    pulled = dotenv_values(root / ".vercel/.env.production.local")
    config = deployment_config(
        json.loads((root / "vercel.json").read_text()),
        pulled.get("APP_ENV") or "",
        pulled.get("DELETED_DATA_CLEANUP_MODE") or "disabled",
        demo_mode=(pulled.get("DEMO_MODE") or "").lower() in {"1", "true", "yes", "on"},
        demo_cleanup_configured=(pulled.get("DEMO_DATA_ISOLATED") or "").lower()
        in {"1", "true", "yes", "on"}
        and bool(pulled.get("DEMO_DATABASE_URL")),
    )
    (root / ".vercel/deploy-config.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
