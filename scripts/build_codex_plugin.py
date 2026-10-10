"""秘密情報を含めず、固定ファイルだけで検証用・申請用ZIPを組み立てる。"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1] / "plugins" / "syncnesto"
FILES = ("assets/icon.svg", "skills/syncnesto-work/SKILL.md")
LISTING_FIELDS = (
    "developerName",
    "websiteURL",
    "supportURL",
    "privacyPolicyURL",
    "termsOfServiceURL",
)


def listing_metadata(path: Path | None) -> dict[str, str]:
    """公開済みの実URLと発行者を明示しない申請パッケージを拒否する。"""
    data = json.loads(path.read_text()) if path else {}
    if not isinstance(data, dict) or set(data) != set(LISTING_FIELDS):
        raise ValueError("申請にはlisting.example.jsonの全項目を確定したJSONが必要です")
    for key in LISTING_FIELDS:
        value = data[key]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"{key}が未確定です")
        if key == "developerName":
            continue
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or any(ord(c) <= 32 for c in value)
            or "\\" in value
        ):
            raise ValueError(f"{key}は資格情報を含まないHTTPS URLで指定してください")
    return data


def build(
    output: Path, *, submission: bool = False, listing: Path | None = None
) -> None:
    """申請用portable形式か、デスクトップ検証用Codex形式を出力する。"""
    manifest = json.loads((ROOT / "plugin.json").read_text())
    mcp = json.loads((ROOT / "mcp.json").read_text())
    extension = manifest["extensions"]["com.openai"]
    payloads = {}
    for name in FILES:
        path = ROOT / name
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("プラグイン資材にsymlinkを使用できません")
        payloads[name] = path.read_bytes()
    if submission:
        metadata = listing_metadata(listing)
        extension["interface"].update(metadata)
        manifest["author"]["name"] = metadata["developerName"]
        payloads["plugin.json"] = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode()
        payloads["mcp.json"] = json.dumps(mcp, indent=2).encode()
    else:
        manifest.pop("$schema")
        manifest["interface"] = extension.pop("interface")
        manifest["skills"] = "./skills/"
        manifest["mcpServers"] = "./.mcp.json"
        server = mcp["mcpServers"]["syncnesto"]
        auth = server.pop("extensions")["com.openai"]["auth"]
        server["type"] = "http"
        server["oauth"] = {
            "clientId": auth["client"]["clientId"],
            "callbackUrl": "http://127.0.0.1/callback",
        }
        mcp.pop("$schema")
        payloads[".codex-plugin/plugin.json"] = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode()
        payloads[".mcp.json"] = json.dumps(mcp, indent=2).encode()
    # 固定のallowlistからのみ収録する。環境変数・.env・ローカル設定を走査しない。
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(payloads.items()):
            info = ZipInfo(name)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)


def main() -> None:
    """出力先の上書きや外部へのアップロードを行わない。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--submission", action="store_true")
    parser.add_argument("--listing", type=Path)
    args = parser.parse_args()
    try:
        build(args.output, submission=args.submission, listing=args.listing)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"作成しました: {args.output}（申請・公開は行っていません）")


if __name__ == "__main__":
    main()
