#!/usr/bin/env python3
"""Render the whole-genome IGV view of a purple_plotter HTML file to SVG + PNG.

Loads the generated ``{sample}.igv.bars.html`` in headless Chromium (Playwright),
switches to the whole-genome view (locus ``all``) and calls the viewer's own
``window.ppExport`` functions, so the files are identical to what the
"Save SVG" / "Save PNG" buttons produce.

No network access is needed or used. The CDN ``igv.min.js`` and the hg38
genome-definition files the viewer requests are served from ``assets/``;
every other request is aborted and reported.

Playwright is imported lazily so that purple_plotter.py and its tests do not
depend on it. The browser is taken from --chromium_path, then $CHROMIUM_PATH,
then Playwright's own browser cache.
"""
import argparse
import base64
import os
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"

# URLs the generated HTML requests -> bundled file (see assets/README.md).
CDN_IGV = "https://igv.org/web/release/3.8.3/dist/igv.min.js"
GENOME_BASE = "https://igv.org/genomes/data/hg38/"
ROUTES = {
    CDN_IGV:                                  ("igv.min.js",         "application/javascript"),
    "https://igv.org/genomes/genomes3.json":  ("genomes.json",       "application/json"),
    "https://assets.invalid/hg38.chrom.sizes": ("hg38.chrom.sizes",  "text/plain"),
    GENOME_BASE + "cytoBandIdeo.txt.gz":      ("cytoBandIdeo.txt.gz", "application/gzip"),
    GENOME_BASE + "hg38_alias.tab":           ("hg38_alias.tab",     "text/plain"),
}


class RenderError(RuntimeError):
    pass


def render_genome_image(
    html_path: Path,
    out_prefix: Path,
    chromium_path: str | None = None,
    locus: str = "all",
    scale: float = 2.0,
    timeout_s: float = 120.0,
    assets_dir: Path = ASSETS,
) -> tuple[Path, Path]:
    """Write ``{out_prefix}.svg`` and ``{out_prefix}.png``; return both paths.

    Raises RenderError if the viewer fails to initialise or any request other
    than the bundled ones is attempted (so a missing asset is not silent).
    """
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    html_path = Path(html_path).resolve()
    out_prefix = Path(out_prefix)
    chromium_path = chromium_path or os.environ.get("CHROMIUM_PATH") or None
    blocked: list[str] = []

    def handle(route):
        url = route.request.url
        entry = ROUTES.get(url)
        if entry is None:
            blocked.append(url)
            route.abort()
            return
        name, ctype = entry
        route.fulfill(
            body=(Path(assets_dir) / name).read_bytes(),
            content_type=ctype,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    launch_kw = {"args": ["--no-sandbox"]}
    if chromium_path:
        launch_kw["executable_path"] = chromium_path

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(**launch_kw)
        except PlaywrightError as e:
            raise RenderError(f"Could not start Chromium: {e}") from e
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.set_default_timeout(timeout_s * 1000)
            page.route(
                lambda u: u.startswith(("http://", "https://")), handle
            )
            console_errors: list[str] = []
            page.on("pageerror", lambda e: console_errors.append(str(e)))
            page.goto(html_path.as_uri())
            try:
                page.wait_for_function("window.igvReady === true || !!window.igvError")
            except PlaywrightError as e:
                raise RenderError(f"igv.js did not initialise: {e}") from e
            err = page.evaluate("window.igvError || null")
            if err:
                raise RenderError(f"igv.js failed to start: {err}")

            if locus:
                page.evaluate("loc => window.igvBrowser.search(loc)", locus)
            svg = page.evaluate("window.ppExport.svg()")
            png_b64 = page.evaluate("s => window.ppExport.png(s)", scale)
        except PlaywrightError as e:
            raise RenderError(f"Rendering failed: {e}") from e
        finally:
            browser.close()

    if blocked:
        raise RenderError(
            "Viewer requested URLs that are not bundled (add them to ROUTES): "
            + ", ".join(sorted(set(blocked)))
        )
    if console_errors:
        raise RenderError("Page errors: " + "; ".join(console_errors))
    if "<svg" not in svg:
        raise RenderError("toSVG() did not return an SVG document")

    svg_path = Path(f"{out_prefix}.svg")
    png_path = Path(f"{out_prefix}.png")
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg)
    png_path.write_bytes(base64.b64decode(png_b64))
    return svg_path, png_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html",          required=True, help="{sample}.igv.bars.html from purple_plotter.py")
    ap.add_argument("--out_prefix",    required=True, help="Output path without extension; .svg and .png are written")
    ap.add_argument("--chromium_path", default=None,  help="Chromium/Chrome executable (default: $CHROMIUM_PATH, then Playwright's)")
    ap.add_argument("--locus",         default="all", help="Locus to render (default: all = whole genome)")
    ap.add_argument("--scale",         type=float, default=2.0, help="PNG pixel scale (default 2)")
    args = ap.parse_args()
    try:
        svg, png = render_genome_image(
            Path(args.html), Path(args.out_prefix),
            chromium_path=args.chromium_path, locus=args.locus, scale=args.scale,
        )
    except RenderError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Written: {svg}  ({svg.stat().st_size // 1024} KB)")
    print(f"Written: {png}  ({png.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
