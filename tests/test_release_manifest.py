"""scripts/release_manifest.py: the release artefacts describe what the app actually ships."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_manifest as rm


def test_requirements_list_every_bundled_wheel_with_its_file_hash():
    wheels = sorted(rm.PACKAGES.glob("*.whl"))
    lines = rm.requirements().splitlines()
    assert len(lines) == len(wheels) > 0
    by_hash = {hashlib.sha256(w.read_bytes()).hexdigest(): w.name for w in wheels}
    for line in lines:
        spec, _, digest = line.partition(" --hash=sha256:")
        name, _, version = spec.partition("==")
        wheel = by_hash[digest]                      # hash is the shipped file's
        assert f"-{version}-" in wheel
        assert wheel.lower().replace("_", "-").startswith(name + "-")


def test_requirements_include_pandas_pin_from_requirements_txt():
    pins = dict(line.split(" ")[0].split("==") for line in rm.requirements().splitlines())
    wanted = [line for line in (ROOT / "requirements.txt").read_text().splitlines()
              if line.startswith("pandas==")]
    assert wanted and pins["pandas"] == wanted[0].split("==")[1]


def test_asset_requirements_match_makefile_pins():
    pins = rm.asset_pins()
    assert ("playwright", "1.64.1") in pins
    assert rm.asset_requirements() == "".join(f"{n}=={v}\n" for n, v in pins)


@pytest.mark.parametrize("name", ["pandas-2.3.2.tar.gz", "pandas.whl", "pandas-2.3.2-py3.whl"])
def test_anything_but_wheels_is_rejected_even_beside_valid_wheels(tmp_path, monkeypatch, name):
    # src/code.sh installs every non-hidden entry, so the inventory must not skip any
    (tmp_path / "six-1.17.0-py2.py3-none-any.whl").write_bytes(b"w")
    (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(rm, "PACKAGES", tmp_path)
    with pytest.raises(ValueError, match=name.replace(".", r"\.")):
        rm.shipped_wheels()


def test_hidden_files_are_not_inventoried(tmp_path, monkeypatch):
    (tmp_path / "six-1.17.0-py2.py3-none-any.whl").write_bytes(b"w")
    (tmp_path / ".gitkeep").write_bytes(b"")
    monkeypatch.setattr(rm, "PACKAGES", tmp_path)
    assert [n for n, _, _ in rm.shipped_wheels()] == ["six"]


def test_code_sh_installs_the_whole_packages_directory():
    assert "pip install --no-index --no-deps /home/dnanexus/packages/*" in (
        ROOT / "src/code.sh").read_text()


def test_check_installed_reports_version_mismatch(monkeypatch):
    real = rm.importlib.metadata.version
    monkeypatch.setattr(rm.importlib.metadata, "version",
                        lambda n: "0.0.1" if n == "pandas" else real(n))
    with pytest.raises(ValueError, match="pandas: ships 2.3.2, installed 0.0.1"):
        rm.check_installed()


def test_wheel_with_build_tag_is_parsed(tmp_path, monkeypatch):
    (tmp_path / "Foo_Bar-1.0-1-py3-none-any.whl").write_bytes(b"x")
    monkeypatch.setattr(rm, "PACKAGES", tmp_path)
    assert rm.shipped_wheels() == [("foo-bar", "1.0", hashlib.sha256(b"x").hexdigest())]


def test_extend_sbom_adds_app_igv_and_asset():
    pytest.importorskip("playwright")   # CI installs the asset's Playwright version
    sbom = rm.extend_sbom({"bomFormat": "CycloneDX", "components": []})
    app = json.loads(rm.DXAPP.read_text())
    assert sbom["metadata"]["component"]["name"] == app["name"]
    assert sbom["metadata"]["component"]["version"] == app["version"]
    igv, asset = sbom["components"]
    assert igv["version"] == rm.igv_js_version()
    assert igv["hashes"][0]["content"] == hashlib.sha256(rm.IGV_JS.read_bytes()).hexdigest()
    (purify,) = igv["components"]
    assert purify["name"] == "dompurify" and re.fullmatch(r"\d+\.\d+\.\d+", purify["version"])
    (record,) = [d["id"] for d in app["runSpec"]["assetDepends"]]
    assert {"name": "dnanexus:record", "value": record} in asset["properties"]
    parts = {c["name"]: c for c in asset["components"]}
    assert parts["playwright"]["version"] == dict(rm.asset_pins())["playwright"]
    assert re.fullmatch(r"\d+(\.\d+){3}", parts["chromium-headless-shell"]["version"])


def test_chromium_version_needs_the_asset_playwright(monkeypatch):
    monkeypatch.setattr(rm.importlib.metadata, "version", lambda n: "0.0.1")
    with pytest.raises(ValueError, match="asset pins playwright"):
        rm.chromium_version()


def test_igv_hash_matches_the_load_time_check():
    src = (rm.PLOTTER / "purple_plotter.py").read_text()
    assert f'IGV_JS_SHA256  = "{hashlib.sha256(rm.IGV_JS.read_bytes()).hexdigest()}"' in src


def test_cli_usage_error_exits_2():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/release_manifest.py"), "nope"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "release_manifest.py requirements" in r.stderr
