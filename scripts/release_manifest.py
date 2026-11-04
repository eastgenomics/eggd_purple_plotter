#!/usr/bin/env python3
"""Release artefacts for the DNAnexus app: what it ships, with hashes, and an SBOM.

The app does not install from requirements.txt. It installs the wheels bundled in
resources/home/dnanexus/packages/, embeds igv.js in every viewer, and uses the
pp_headless_chromium asset (pinned by record ID in dxapp.json) for image export.

  release_manifest.py requirements        shipped wheels as name==version --hash=sha256:...
  release_manifest.py asset-requirements  Python packages installed in the asset (asset/Makefile PINS)
  release_manifest.py extend-sbom FILE    add the app, igv.js and the asset to a CycloneDX JSON SBOM
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "resources/home/dnanexus/packages"
PLOTTER = ROOT / "resources/home/dnanexus/purple_plotter"
IGV_JS = PLOTTER / "assets/igv.min.js"
DXAPP = ROOT / "dxapp.json"
ASSET_JSON = ROOT / "asset/dxasset.json"
ASSET_MAKEFILE = ROOT / "asset/Makefile"

# PEP 427: {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
WHEEL = re.compile(r"^(?P<name>[^-]+)-(?P<version>[^-]+)(-\d[^-]*)?-[^-]+-[^-]+-[^-]+\.whl$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shipped_wheels() -> list[tuple[str, str, str]]:
    """(name, version, sha256) for every bundled wheel, sorted by name."""
    wheels = []
    for whl in sorted(PACKAGES.glob("*.whl")):
        m = WHEEL.match(whl.name)
        if not m:
            raise ValueError(f"not a wheel file name: {whl.name}")
        name = re.sub(r"[-_.]+", "-", m["name"]).lower()
        wheels.append((name, m["version"], sha256(whl)))
    if not wheels:
        raise ValueError(f"no wheels in {PACKAGES}")
    return sorted(wheels)


def asset_pins() -> list[tuple[str, str]]:
    """(name, version) from the PINS= line of asset/Makefile."""
    m = re.search(r"^PINS=(.+)$", ASSET_MAKEFILE.read_text(), re.M)
    if not m:
        raise ValueError("no PINS= line in asset/Makefile")
    pins = []
    for spec in m[1].split():
        name, sep, version = spec.partition("==")
        if not sep or not name or not version:
            raise ValueError(f"asset pin is not name==version: {spec}")
        pins.append((name.lower(), version))
    return pins


def igv_js_version() -> str:
    m = re.search(r'^IGV_JS_VERSION\s*=\s*"([^"]+)"', (PLOTTER / "purple_plotter.py").read_text(), re.M)
    if not m:
        raise ValueError("IGV_JS_VERSION not found in purple_plotter.py")
    return m[1]


def requirements() -> str:
    return "".join(f"{n}=={v} --hash=sha256:{h}\n" for n, v, h in shipped_wheels())


def asset_requirements() -> str:
    return "".join(f"{n}=={v}\n" for n, v in asset_pins())


def extend_sbom(sbom: dict) -> dict:
    """Set the app as the SBOM's subject and add the components not in the wheel list."""
    app = json.loads(DXAPP.read_text())
    asset = json.loads(ASSET_JSON.read_text())
    (dep,) = [d for d in app["runSpec"]["assetDepends"] if d["name"] == asset["name"]]
    if dep["version"] != asset["version"]:
        raise ValueError(f"dxapp.json pins {asset['name']} {dep['version']}, "
                         f"asset/dxasset.json is {asset['version']}")
    igv_version = igv_js_version()
    sbom.setdefault("metadata", {})["component"] = {
        "type": "application", "bom-ref": "app", "name": app["name"], "version": app["version"],
        "description": "DNAnexus app",
    }
    components = sbom.setdefault("components", [])
    components.append({
        "type": "library", "bom-ref": "igv.js", "name": "igv", "version": igv_version,
        "purl": f"pkg:npm/igv@{igv_version}",
        "hashes": [{"alg": "SHA-256", "content": sha256(IGV_JS)}],
        "licenses": [{"license": {"id": "MIT"}}],
        "description": "igv.min.js, embedded in every generated viewer; includes DOMPurify "
                       "(Apache-2.0 / MPL-2.0)",
    })
    components.append({
        "type": "application", "bom-ref": "asset", "name": asset["name"],
        "version": asset["version"],
        "description": f"DNAnexus asset {dep['id']}: {asset['description']}",
        "properties": [{"name": "dnanexus:record", "value": dep["id"]},
                       {"name": "dnanexus:release", "value": f"{asset['distribution']} {asset['release']}"}],
        "components": [
            {"type": "library", "bom-ref": f"asset/{n}", "name": n, "version": v,
             "purl": f"pkg:pypi/{n}@{v}"}
            for n, v in asset_pins()
        ] + [{
            "type": "application", "bom-ref": "asset/chromium-headless-shell",
            "name": "chromium-headless-shell",
            "description": "Chromium build installed by the asset's Playwright "
                           "(playwright install --only-shell chromium); its version is the "
                           "one that Playwright release pins",
        }],
    })
    return sbom


def main(argv: list[str]) -> int:
    if argv == ["requirements"]:
        sys.stdout.write(requirements())
    elif argv == ["asset-requirements"]:
        sys.stdout.write(asset_requirements())
    elif len(argv) == 2 and argv[0] == "extend-sbom":
        path = Path(argv[1])
        path.write_text(json.dumps(extend_sbom(json.loads(path.read_text())), indent=2) + "\n")
    else:
        sys.stderr.write(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
