"""配布ZIPの秘密混入防止と、公開前の不足情報を検証する。"""

import json
from zipfile import ZipFile

import pytest

from app.core.mcp import PLUGIN_CLIENT_ID
from app.services.mcp_catalog import TOOLS
from app.services.mcp_development_catalog import DEVELOPMENT_TOOLS
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
            assert set(case["tools_triggered"].split(", ")) <= set(TOOLS) | set(
                DEVELOPMENT_TOOLS
            )


def test_portable_draft_omits_unconfirmed_urls_and_keeps_authenticated_mcp(tmp_path):
    """非公開の下書きを作れても、未確定のURLや認証なしの設定を捏造しない。"""
    output = tmp_path / "draft.zip"
    build(output, draft=True)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {*FILES, "plugin.json", "mcp.json"}
        manifest = json.loads(archive.read("plugin.json"))
        interface = manifest["extensions"]["com.openai"]["interface"]
        for key in ("supportURL", "privacyPolicyURL", "termsOfServiceURL"):
            assert key not in interface
        server = json.loads(archive.read("mcp.json"))["mcpServers"]["syncnesto"]
        assert server["type"] == "streamable-http"
        assert server["extensions"]["com.openai"]["auth"]["type"] == "oauth"
        assert (
            server["extensions"]["com.openai"]["auth"]["client"]["clientId"]
            == PLUGIN_CLIENT_ID
        )
    with pytest.raises(ValueError):
        build(tmp_path / "submission.zip", submission=True)


@pytest.mark.parametrize("kwargs", [{"submission": True}, {"listing": ROOT}])
def test_portable_draft_cannot_override_submission_validation(tmp_path, kwargs):
    """下書きモードで審査提出用の確定情報チェックを迂回しない。"""
    output = tmp_path / "invalid.zip"
    with pytest.raises(ValueError):
        build(output, draft=True, **kwargs)
    assert not output.exists()


@pytest.fixture
def approved_package_inputs(tmp_path):
    """既存掲載情報と審査補助情報を持つ架空の承認済みZIP。"""
    listing = tmp_path / "listing.json"
    listing.write_text(
        json.dumps(
            {
                "developerName": "Test Publisher",
                **{
                    key: "https://test.example/" + key
                    for key in (
                        "websiteURL",
                        "supportURL",
                        "privacyPolicyURL",
                        "termsOfServiceURL",
                    )
                },
            }
        )
    )
    manifest = json.loads((ROOT / "plugin.json").read_text())
    manifest["version"] = "0.1.1"
    extension = manifest["extensions"]["com.openai"]
    extension["review"] = {
        "demo_recording_url": "https://test.example/review-video",
        "commerce": False,
        "reviewer_password": "must-not-be-copied",
    }
    extension["publication"]["countries"] = ["JP"]
    return listing, manifest, json.loads((ROOT / "mcp.json").read_text())


def test_approved_baseline_keeps_review_material_without_credentials(
    tmp_path, approved_package_inputs
):
    """確定済み動画等を保持し、任意の旧フィールドやOAuth秘密は転記しない。"""
    listing, manifest, mcp = approved_package_inputs
    baseline = tmp_path / "approved.zip"
    with ZipFile(baseline, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("mcp.json", json.dumps(mcp))
    output = tmp_path / "new.zip"
    build(output, submission=True, listing=listing, approved_package=baseline)
    with ZipFile(output) as archive:
        extension = json.loads(archive.read("plugin.json"))["extensions"]["com.openai"]
        assert (
            extension["review"]["demo_recording_url"]
            == "https://test.example/review-video"
        )
        assert extension["review"]["commerce"] is False
        assert "reviewer_password" not in extension["review"]
        assert extension["publication"]["countries"] == ["JP"]
        assert len(extension["review"]["test_cases"]["positive"]) == 9
        assert set(archive.namelist()) == {*FILES, "plugin.json", "mcp.json"}
    with pytest.raises(ValueError):
        build(tmp_path / "invalid-mode.zip", approved_package=baseline)


@pytest.mark.parametrize("changed", ["name", "mcp"])
def test_approved_baseline_rejects_a_different_package_or_connection(
    tmp_path, approved_package_inputs, changed
):
    """承認ZIPの取り違えや接続先変更を黙って合成しない。"""
    listing, manifest, mcp = approved_package_inputs
    if changed == "name":
        manifest["name"] = "other-package"
    else:
        mcp["mcpServers"]["syncnesto"]["url"] = "https://other.example/mcp"
    baseline = tmp_path / "wrong.zip"
    with ZipFile(baseline, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("mcp.json", json.dumps(mcp))
    output = tmp_path / "new.zip"
    with pytest.raises(ValueError):
        build(output, submission=True, listing=listing, approved_package=baseline)
    assert not output.exists()
