#!/usr/bin/env python3
"""Render the whole-genome IGV view of a purple_plotter HTML file to SVG + PNG.

Loads the generated ``{sample}.igv.bars.html`` in headless Chromium (Playwright),
switches to the whole-genome view (locus ``all``) and calls the viewer's own
``window.ppExport`` functions, so the files are identical to what the
"Save SVG" / "Save PNG" buttons produce.

No network access is needed or used. igv.js is embedded in the HTML itself; the
hg38 genome-definition files the viewer requests are served from ``assets/``;
every other request is aborted and reported. The requested URLs are on hosts the
viewer's Content-Security-Policy allows (igv.org, hgdownload.soe.ucsc.edu), because
the CSP is checked before Playwright's routing sees a request.

Playwright is imported lazily so that purple_plotter.py and its tests do not
depend on it. The browser is taken from --chromium_path, then $CHROMIUM_PATH,
then Playwright's own browser cache.
"""
import argparse
import base64
import os
import sys
from pathlib import Path
from urllib.parse import quote

ASSETS = Path(__file__).resolve().parent / "assets"

# URLs the generated HTML requests -> bundled file (see assets/README.md).
# igv.min.js is no longer requested (it is inlined); a request for it would be a bug and is
# reported like any other unbundled URL.
GENOME_BASE = "https://igv.org/genomes/data/hg38/"
# Not an igv.org URL on purpose: igv.js replaces any igv.org hg38 fastaURL with UCSC's
# hg38.2bit, which is not bundled.
CHROM_SIZES = "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/hg38.chrom.sizes"
ROUTES = {
    "https://igv.org/genomes/genomes3.json":  ("genomes.json",       "application/json"),
    CHROM_SIZES:                              ("hg38.chrom.sizes",   "text/plain"),
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

    ``locus`` is applied before the viewer starts (via ``?locus=``), whatever
    initial locus the HTML was generated with. Raises RenderError, naming the
    URLs and page errors involved, if the viewer fails to initialise or any
    request other than the bundled ones is attempted.
    """
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    html_path = Path(html_path).resolve()
    out_prefix = Path(out_prefix)
    chromium_path = chromium_path or os.environ.get("CHROMIUM_PATH") or None
    blocked: list[str] = []
    page_errors: list[str] = []
    csp_violations: list[str] = []

    def diagnostics() -> str:
        parts = []
        if blocked:
            parts.append("requests not bundled (add to ROUTES): " + ", ".join(sorted(set(blocked))))
        if page_errors:
            parts.append("page errors: " + "; ".join(page_errors))
        if csp_violations:
            parts.append("Content-Security-Policy violations: " + "; ".join(csp_violations))
        return " | ".join(parts)

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
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            # The viewer's CSP can block a request or script before routing or "pageerror"
            # sees it; record violations so a failure names its cause.
            page.expose_function("__ppCspViolation", lambda v: csp_violations.append(v))
            page.add_init_script(
                'document.addEventListener("securitypolicyviolation",'
                ' e => window.__ppCspViolation(e.violatedDirective + " " + e.blockedURI));')

            # The viewer reads ?locus= before creating the browser, so the first
            # (and only) render is at `locus` and never asks the network to resolve
            # the HTML's own initial locus (e.g. a gene name).
            page.goto(html_path.as_uri() + "?locus=" + quote(locus or "all", safe=""))

            # Poll so an unbundled request or a script error fails fast, with its cause.
            waited = 0.0
            while True:
                state = page.evaluate("window.igvReady === true ? 'ready' : (window.igvError || null)")
                if state == "ready":
                    break
                if state or blocked or page_errors or csp_violations or waited >= timeout_s:
                    why = f"igv.js failed to start: {state}" if state else (
                        "igv.js did not initialise" + (" (timed out)" if waited >= timeout_s else ""))
                    raise RenderError(why + (f" [{diagnostics()}]" if diagnostics() else ""))
                page.wait_for_timeout(250)
                waited += 0.25

            svg = page.evaluate("window.ppExport.svg()")
            png_b64 = page.evaluate("s => window.ppExport.png(s)", scale)
        except PlaywrightError as e:
            page.wait_for_timeout(100)   # let a pending securitypolicyviolation event arrive
            raise RenderError(f"Rendering failed: {e}" + (f" [{diagnostics()}]" if diagnostics() else "")) from e
        finally:
            browser.close()

    if blocked or page_errors or csp_violations:
        raise RenderError(diagnostics())
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
