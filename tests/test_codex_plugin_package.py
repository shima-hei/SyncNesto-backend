"""配布ZIPの秘密混入防止と、公開前の不足情報を検証する。"""

import json
from zipfile import ZipFile

import pytest

from app.core.mcp import PLUGIN_CLIENT_ID
from app.services.mcp_catalog import TOOLS
from scripts.build_codex_plugin import FILES, ROOT, build

pytestmark = pytest.mark.no_db


def test_codex_package_is_remote_and_has_no_unrelated_files(tmp_path):
    """ローカルMCP起動や秘密の埋め込みをせず既存公開URLへ接続する。"""
    output = tmp_path / "codex.zip"
    build(output)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            *FILES,
            ".codex-plugin/plugin.json",
            ".mcp.json",
        }
        server = json.loads(archive.read(".mcp.json"))["mcpServers"]["syncnesto"]
        assert server == {
            "type": "http",
            "url": "https://syncnesto-api.vercel.app/mcp",
            "oauth": {
                "clientId": PLUGIN_CLIENT_ID,
                "callbackUrl": "http://127.0.0.1/callback",
            },
        }
        manifest = json.loads(archive.read(".codex-plugin/plugin.json"))
        assert manifest["mcpServers"] == "./.mcp.json"
        for key in ["logo", "composerIcon"]:
            assert manifest["interface"][key].removeprefix("./") in archive.namelist()
        assert (
            manifest["extensions"]["com.openai"]["onboardingSkill"].removeprefix("./")
            in archive.namelist()
        )


def test_submission_requires_real_listing_and_preserves_oauth(tmp_path):
    """未確定のまま申請用と誤表示せず、明示された紹介情報のみ反映する。"""
    output = tmp_path / "submission.zip"
    with pytest.raises(ValueError):
        build(output, submission=True)
    with pytest.raises(ValueError):
        build(output, submission=True, listing=ROOT.parent / "listing.example.json")
    assert not output.exists()
    listing = tmp_path / "listing.json"
    metadata = {
        "developerName": "Test Publisher",
        **{
            key: "https://test.example/" + key
            for key in [
                "websiteURL",
                "supportURL",
                "privacyPolicyURL",
                "termsOfServiceURL",
            ]
        },
    }
    listing.write_text(json.dumps(metadata))
    build(output, submission=True, listing=listing)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {*FILES, "plugin.json", "mcp.json"}
        manifest = json.loads(archive.read("plugin.json"))
        assert (
            manifest["extensions"]["com.openai"]["interface"]["privacyPolicyURL"]
            == metadata["privacyPolicyURL"]
        )
        server = json.loads(archive.read("mcp.json"))["mcpServers"]["syncnesto"]
        assert server["type"] == "streamable-http"
        auth = server["extensions"]["com.openai"]["auth"]
        assert auth["client"]["clientId"] == PLUGIN_CLIENT_ID
        assert auth["client"]["tokenEndpointAuthMethod"] == "none"
        for case in manifest["extensions"]["com.openai"]["review"]["test_cases"][
            "positive"
        ]:
            assert set(case["tools_triggered"].split(", ")) <= set(TOOLS)
