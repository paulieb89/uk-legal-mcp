"""The version guard must pass on the real repo and fail on a real mismatch."""

import json
import shutil
from pathlib import Path

from scripts.check_version_sync import ROOT, check


def _copy_manifests(tmp_path: Path) -> Path:
    for name in ("pyproject.toml", "server.json"):
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def test_repo_versions_agree():
    assert check() == []


def test_server_json_package_drift_is_caught(tmp_path):
    root = _copy_manifests(tmp_path)
    server = json.loads((root / "server.json").read_text())
    server["packages"][0]["version"] = "0.0.1"
    (root / "server.json").write_text(json.dumps(server))
    problems = check(root)
    assert len(problems) == 1
    assert "server.json packages[0].version" in problems[0]


def test_tag_mismatch_is_caught_and_matching_tag_passes(tmp_path):
    root = _copy_manifests(tmp_path)
    current = json.loads((root / "server.json").read_text())["version"]
    assert check(root, expect=f"v{current}") == []
    assert "release tag 'v0.0.1'" in check(root, expect="v0.0.1")[0]


def test_unreadable_manifest_fails_closed(tmp_path):
    problems = check(tmp_path)
    assert problems and "cannot read" in problems[0]
