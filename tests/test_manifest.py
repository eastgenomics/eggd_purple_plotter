"""dxapp.json release metadata: the version fields agree and are a plain release version."""
import json
import re
from pathlib import Path

DXAPP = Path(__file__).parent.parent / "dxapp.json"


def test_version_and_github_release_agree():
    spec = json.loads(DXAPP.read_text())
    assert spec["properties"]["githubRelease"] == spec["version"]


def test_version_is_plain_semver():
    # A +build or -pre suffix in the manifest would be released as such; the suffix
    # dx build can add on its own is caught by the release procedure in README.
    assert re.fullmatch(r"\d+\.\d+\.\d+", json.loads(DXAPP.read_text())["version"])
