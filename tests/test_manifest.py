"""dxapp.json release metadata: the version fields agree and are a plain release version."""
import json
import re
from pathlib import Path

import pytest

DXAPP = Path(__file__).parent.parent / "dxapp.json"
# X.Y.Z with ASCII digits and no leading zeroes; no -pre or +build suffix.
PLAIN_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def test_version_and_github_release_agree():
    spec = json.loads(DXAPP.read_text())
    assert spec["properties"]["githubRelease"] == spec["version"]


def test_version_is_plain_semver():
    # A suffix in the manifest would be released as such. dx build adds none when run with
    # --no-version-autonumbering (README "Release").
    assert PLAIN_VERSION.fullmatch(json.loads(DXAPP.read_text())["version"])


@pytest.mark.parametrize("v", ["1.1.1", "0.10.0", "10.0.3"])
def test_plain_version_pattern_accepts(v):
    assert PLAIN_VERSION.fullmatch(v)


@pytest.mark.parametrize("v", ["1.1.0+build.20261104.0006", "1.1.1-rc1", "01.02.03", "1.1",
                               "\u0661.\u0661.\u0661", "v1.1.1"])
def test_plain_version_pattern_rejects(v):
    assert not PLAIN_VERSION.fullmatch(v)
