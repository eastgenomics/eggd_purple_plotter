#!/usr/bin/env python3
"""Release artefacts for the DNAnexus app: what it ships, with hashes, and an SBOM.

The app does not install from requirements.txt. It installs the wheels bundled in
resources/home/dnanexus/packages/, embeds igv.js in every viewer, and uses the
pp_headless_chromium asset (pinned by record ID in dxapp.json) for image export.

  release_manifest.py requirements        shipped wheels as name==version --hash=sha256:...
  release_manifest.py asset-requirements  Python packages installed in the asset (asset/Makefile PINS)
  release_manifest.py check-installed     fail unless this Python has exactly the shipped versions
  release_manifest.py extend-sbom FILE    add the app, igv.js and the asset to a CycloneDX JSON SBOM
                                          (needs the asset's Playwright version installed, to read
                                          the Chromium version it pins)
"""
import hashlib
import importlib.metadata
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
    """(name, version, sha256) for every bundled wheel, sorted by name.

    src/code.sh installs every non-hidden entry of the packages directory, so anything there
    that is not a wheel is an error rather than something to leave out of the inventory."""
    wheels = []
    for whl in sorted(f for f in PACKAGES.iterdir() if not f.name.startswith(".")):
        m = WHEEL.match(whl.name) if whl.is_file() else None
        if not m:
            raise ValueError(f"not a wheel file: {whl.name}")
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


def dompurify_version() -> str:
    """The DOMPurify release bundled inside igv.min.js, from its licence banner."""
    m = re.search(rb"@license DOMPurify (\d+\.\d+\.\d+) ", IGV_JS.read_bytes())
    if not m:
        raise ValueError("no DOMPurify licence banner in igv.min.js")
    return m[1].decode()


def chromium_version() -> str:
    """Chromium headless shell version pinned by the asset's Playwright release."""
    (playwright,) = [v for n, v in asset_pins() if n == "playwright"]
    installed = importlib.metadata.version("playwright")
    if installed != playwright:
        raise ValueError(f"asset pins playwright {playwright}, this Python has {installed}")
    import playwright as pw
    browsers = json.loads((Path(pw.__file__).parent / "driver/package/browsers.json").read_text())
    (shell,) = [b for b in browsers["browsers"] if b["name"] == "chromium-headless-shell"]
    return shell["browserVersion"]


def check_installed() -> None:
    """Raise unless every shipped wheel's version is the one installed here."""
    wrong = []
    for name, version, _ in shipped_wheels():
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            have = None
        if have != version:
            wrong.append(f"{name}: ships {version}, installed {have}")
    if wrong:
        raise ValueError("test environment differs from what ships: " + "; ".join(wrong))


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
    purify = dompurify_version()
    chromium = chromium_version()
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
        "description": "igv.min.js, embedded in every generated viewer",
        "components": [{
            "type": "library", "bom-ref": "igv.js/dompurify", "name": "dompurify",
            "version": purify, "purl": f"pkg:npm/dompurify@{purify}",
            "licenses": [{"expression": "Apache-2.0 OR MPL-2.0"}],
            "description": "bundled inside igv.min.js",
        }],
    })
    components.append({
        "type": "application", "bom-ref": "asset", "name": asset["name"],
        "version": asset["version"],
        "description": f"DNAnexus asset {dep['id']}: {asset['description']}",
        "properties": [{"name": "dnanexus:record", "value": dep["id"]},
                       {"name": "dnanexus:release", "value": f"{asset['distribution']} {asset['release']}"},
                       {"name": "inventory:source",
                        "value": "asset/Makefile and asset/dxasset.json at this commit; expected "
                                 "contents of the record, not verified against it"}],
        "components": [
            {"type": "library", "bom-ref": f"asset/{n}", "name": n, "version": v,
             "purl": f"pkg:pypi/{n}@{v}"}
            for n, v in asset_pins()
        ] + [{
            "type": "application", "bom-ref": "asset/chromium-headless-shell",
            "name": "chromium-headless-shell", "version": chromium,
            "cpe": f"cpe:2.3:a:google:chrome:{chromium}:*:*:*:*:*:*:*",
            "description": "installed by the asset's Playwright (playwright install --only-shell "
                           "chromium); version from that Playwright release's browsers.json. "
                           "The Ubuntu libraries it needs (playwright install-deps) are not listed",
        }],
    })
    return sbom


def main(argv: list[str]) -> int:
    if argv == ["requirements"]:
        sys.stdout.write(requirements())
    elif argv == ["asset-requirements"]:
        sys.stdout.write(asset_requirements())
    elif argv == ["check-installed"]:
        check_installed()
    elif len(argv) == 2 and argv[0] == "extend-sbom":
        path = Path(argv[1])
        path.write_text(json.dumps(extend_sbom(json.loads(path.read_text())), indent=2) + "\n")
    else:
        sys.stderr.write(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
