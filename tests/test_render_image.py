"""Headless whole-genome image rendering.

Needs Playwright and a Chromium binary (``$CHROMIUM_PATH``, a chromium/chrome on
PATH, or Playwright's own browser). Skipped when either is missing, so the normal
CI job (which installs only pandas + pytest) is unaffected.
"""
import base64
import os
import shutil
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"
sys.path.insert(0, str(PKG))

import render_image
from igv_ui import save_via_igv_menu
from purple_plotter import PlotInputs, QCData, MSIData, load_cnvkit_data, render_html, write_html

FIXTURES = Path(__file__).parent / "test_data"


def test_bundled_assets_exist_for_every_route():
    for name, _ctype in render_image.ROUTES.values():
        assert (render_image.ASSETS / name).stat().st_size > 0, name


def _chromium():
    p = os.environ.get("CHROMIUM_PATH")
    if p:
        return p
    for exe in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def _skip_unless_browser():
    pytest.importorskip("playwright.sync_api")
    exe = _chromium()
    if exe is None:
        pytest.skip("no Chromium available (set CHROMIUM_PATH)")
    return exe


@pytest.fixture(scope="module")
def viewer(tmp_path_factory):
    exe = _skip_unless_browser()
    out = tmp_path_factory.mktemp("viewer")
    inputs = PlotInputs(
        sample_id="IMG_TEST",
        locus="chr7:116,672,196-117,120,285",  # not 'all': the renderer must still emit whole genome
        qc=QCData(purity=0.79, ploidy=2.02, status="NORMAL", loaded=True),
        msi=MSIData(),
        cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", None, None),
    )
    html = write_html(inputs, out)
    return exe, html, out


def test_render_genome_image_writes_labelled_svg_and_png(viewer):
    exe, html, out = viewer
    svg, png = render_image.render_genome_image(html, out / "IMG_TEST.genome", chromium_path=exe)
    svg_text = svg.read_text()
    assert svg_text.lstrip().startswith("<svg")
    assert "IMG_TEST" in svg_text and "Purity 79%" in svg_text   # sample banner present
    assert ">X<" in svg_text and ">Y<" in svg_text               # whole-genome ruler, not a single locus
    assert "CNVkit" in svg_text                                  # track content, not just the banner
    data = png.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    assert width >= 2000                                         # 2x scale of a ~1600px view


def test_unbundled_request_is_reported_with_url(viewer, tmp_path, monkeypatch):
    """Drop one bundled file from the routes: the render must fail and name that URL."""
    exe, html, out = viewer
    missing = render_image.CHROM_SIZES
    monkeypatch.setattr(render_image, "ROUTES",
                        {k: v for k, v in render_image.ROUTES.items() if k != missing})
    with pytest.raises(render_image.RenderError) as ei:
        render_image.render_genome_image(html, tmp_path / "x", chromium_path=exe, timeout_s=30)
    assert missing in str(ei.value)


def test_igv_js_is_never_requested(viewer, tmp_path, monkeypatch):
    """igv.js is inlined: no route for it exists, and the render still succeeds."""
    exe, html, out = viewer
    assert not any("igv.min.js" in u for u in render_image.ROUTES)
    assert "igv.min.js" not in html.read_text().split("<script>")[0]
    render_image.render_genome_image(html, tmp_path / "ok", chromium_path=exe)


def test_gene_name_initial_locus_does_not_need_network(tmp_path_factory):
    """A gene-name locus in the HTML would call the online locus service; the renderer must override it."""
    exe = _skip_unless_browser()
    out = tmp_path_factory.mktemp("gene")
    html = write_html(PlotInputs(sample_id="GENE", locus="BRCA1",
                                 cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", None, None)), out)
    svg, png = render_image.render_genome_image(html, out / "g", chromium_path=exe)
    assert ">X<" in svg.read_text()          # whole-genome ruler


@pytest.mark.parametrize("sid", [
    "S" + "-".join(f"{i:04d}ABCDEFGH" for i in range(20)),     # long, breakable at hyphens only
    "W" * 140,                                                  # one very wide unbreakable token
    "\U0001F600" * 90,                                           # astral characters: never split a surrogate pair
])
def test_long_sample_id_is_wrapped_inside_the_image(tmp_path_factory, sid):
    """Every banner line, measured as rendered by Chromium, must fit inside the image width."""
    exe = _skip_unless_browser()
    from playwright.sync_api import sync_playwright
    out = tmp_path_factory.mktemp("long")
    html = out / "page.html"                    # sid may be longer than a filename may be
    html.write_text(render_html(PlotInputs(sample_id=sid, cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", None, None))))
    svg, _png = render_image.render_genome_image(html, out / "l", chromium_path=exe)
    text = svg.read_text()
    import re
    lines = re.findall(r"<text[^>]*font-weight=\"bold\"[^>]*>([^<]*)</text>", text)
    banner = "".join(lines).replace(" ", "")
    assert "\ufffd" not in text
    assert sid.replace(" ", "") in banner, "sample ID not preserved across wrapped lines"

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = b.new_page()
            page.set_content(text)
            boxes = page.evaluate("""() => {
                const svg = document.querySelector('svg');
                const vw = svg.viewBox.baseVal.width;
                return [vw, [...svg.children].filter(e => e.tagName === 'text')
                                .map(t => { const r = t.getBBox(); return [r.x + r.width, t.textContent.slice(0, 20)]; })];
            }""")
        finally:
            b.close()
    width, texts = boxes
    assert texts, "no banner text found"
    for right, label in texts:
        assert right <= width, f"banner text {label!r} runs to {right:.0f}px of {width:.0f}px"


def test_banner_states_genome_and_locus(tmp_path_factory):
    exe = _skip_unless_browser()
    out = tmp_path_factory.mktemp("locus")
    html = write_html(PlotInputs(sample_id="LOC", cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", None, None)), out)
    svg, _ = render_image.render_genome_image(html, out / "w", chromium_path=exe)
    assert "Genome hg38" in svg.read_text() and "Locus: all (whole genome)" in svg.read_text()
    svg2, _ = render_image.render_genome_image(html, out / "r", chromium_path=exe, locus="chr7:116,672,196-117,120,285")
    assert "Locus: chr7:116672196-117120285" in svg2.read_text()
    svg3, _ = render_image.render_genome_image(html, out / "p", chromium_path=exe, locus="all:1000000000-2000000000")
    where = svg3.read_text().split("Locus: ")[1][:40]
    assert "whole genome" not in where and where.startswith("all:")      # partial 'all' view keeps its range


def test_cnvkit_only_viewer_initialises_and_igv_save_menu_downloads(viewer):
    """Empty PURPLE tracks are pruned (igv.js aborts on empty SEG), and igv.js's own Save Image menu
    saves the bannered export under a sample-based name (not igv's plain igvjs.svg/png)."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright

    def handle(route):
        entry = render_image.ROUTES.get(route.request.url)
        if entry is None:
            route.abort()
            return
        route.fulfill(body=(render_image.ASSETS / entry[0]).read_bytes(),
                      content_type=entry[1], headers={"Access-Control-Allow-Origin": "*"})

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = b.new_page()
            page.route(lambda u: u.startswith("http"), handle)
            page.goto(html.resolve().as_uri())
            page.wait_for_function("() => window.igvReady === true || !!window.igvError", timeout=60000)   # function form: a string predicate is eval()ed, which the page CSP blocks
            assert page.evaluate("window.igvError || null") is None
            dl_svg = save_via_igv_menu(page, "SVG")
            assert dl_svg.suggested_filename.startswith("IMG_TEST.") and dl_svg.suggested_filename.endswith(".svg")
            svg = Path(dl_svg.path()).read_text(encoding="utf-8")
            assert "IMG_TEST" in svg and "Purity 79%" in svg and "Genome hg38" in svg   # banner present
            dl_png = save_via_igv_menu(page, "PNG")
            assert dl_png.suggested_filename.startswith("IMG_TEST.") and dl_png.suggested_filename.endswith(".png")
            png = Path(dl_png.path()).read_bytes()
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            # The PNG is the bannered SVG drawn at 2x: its size equals 2 x the SVG's (banner included),
            # which igv.js's own PNG export (browser area only, at devicePixelRatio) would not match.
            import re as _re
            w, h = (float(x) for x in _re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups())
            pw, ph = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
            banner = page.evaluate("""() => { const s = window.ppExport.svg();
                const d = new DOMParser().parseFromString(s, "image/svg+xml").documentElement;
                return d.querySelector("rect").getAttribute("height"); }""")
            assert float(banner) > 0
            assert (pw, ph) == (round(2 * w), round(2 * h))
            # Decode the downloaded PNG and check its pixels: the banner rows are the banner colour
            # (#1a2638), and below the banner the image is the white igv.js background.
            import base64 as _b64
            px = page.evaluate("""async (b64) => {
                const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
                const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
                const x = c.getContext("2d"); x.drawImage(img, 0, 0);
                const at = (px, py) => Array.from(x.getImageData(px, py, 1, 1).data.slice(0, 3));
                // Text pixels: light pixels inside the banner's title line (the text is #e8edf3 on #1a2638).
                const band = x.getImageData(0, 0, Math.min(img.width, 800), 40).data;
                let light = 0;
                for (let i = 0; i < band.length; i += 4) if (band[i] + band[i + 1] + band[i + 2] > 450) light++;
                return { top: at(img.width - 4, 4), belowBanner: at(img.width - 4, img.height - 4), light };
            }""", _b64.b64encode(png).decode())
            assert px["top"] == [0x1a, 0x26, 0x38], px
            assert px["belowBanner"] != [0x1a, 0x26, 0x38], px
            assert px["light"] > 200, px                     # the banner carries rendered text, not a blank band
        finally:
            b.close()


def test_csp_violation_is_reported(viewer, tmp_path):
    """A script the CSP blocks (here: viewer code that no longer matches its hash) fails the render
    promptly and names the violation, rather than timing out with no cause."""
    exe, html, out = viewer
    tampered = tmp_path / "t.html"
    tampered.write_text(html.read_text(encoding="utf-8").replace(
        '"use strict";', '"use strict"; /* changed */', 1), encoding="utf-8")
    with pytest.raises(render_image.RenderError) as ei:
        render_image.render_genome_image(tampered, tmp_path / "t", chromium_path=exe, timeout_s=30)
    assert "Content-Security-Policy violations" in str(ei.value) and "script-src" in str(ei.value)


def test_csp_violation_during_export_is_reported(viewer, tmp_path):
    """If the CSP blocks the PNG export (here: blob: images removed from img-src), the error names it."""
    exe, html, out = viewer
    tampered = tmp_path / "e.html"
    tampered.write_text(html.read_text(encoding="utf-8").replace("img-src data: blob:", "img-src data:", 1),
                        encoding="utf-8")
    with pytest.raises(render_image.RenderError) as ei:
        render_image.render_genome_image(tampered, tmp_path / "e", chromium_path=exe, timeout_s=30)
    assert "Content-Security-Policy violations" in str(ei.value) and "img-src" in str(ei.value)


def _held_viewer_page(p, exe, html, hold_suffix):
    """Open the viewer with the request ending in hold_suffix held (fulfilled later, or aborted)."""
    held = []

    def handle(route):
        entry = render_image.ROUTES.get(route.request.url)
        if entry is None:
            route.abort()
        elif route.request.url.endswith(hold_suffix):
            held.append((route, entry))
        else:
            route.fulfill(body=(render_image.ASSETS / entry[0]).read_bytes(),
                          content_type=entry[1], headers={"Access-Control-Allow-Origin": "*"})

    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    page = b.new_page()
    page.route(lambda u: u.startswith("http"), handle)
    page.goto(html.resolve().as_uri())
    for _ in range(40):                                  # until the held request has been made
        if held:
            break
        page.wait_for_timeout(100)
    assert held, f"{hold_suffix} request not seen"
    page.wait_for_timeout(500)                           # toolbar has been drawn by now
    return b, page, held


def _toolbar_unusable(page):
    from playwright.sync_api import TimeoutError as PWTimeout
    assert page.evaluate("window.igvReady || false") is False
    assert page.evaluate("document.getElementById('igv-div').inert") is True
    assert page.evaluate("getComputedStyle(document.getElementById('igv-div')).opacity") == "0"
    assert page.get_by_text("Save Image", exact=True).count() >= 1      # igv has drawn it ...
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    with pytest.raises(PWTimeout):                                       # ... but it cannot be clicked
        page.get_by_text("Save Image", exact=True).first.click(timeout=1500)
    page.wait_for_timeout(300)
    assert downloads == []


def test_viewer_hidden_until_save_overrides_are_installed(viewer):
    """igv.js shows its toolbar before createBrowser resolves; while the chromosome sizes are still
    loading the viewer must be invisible and unusable, then save the bannered image once ready."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b, page, held = _held_viewer_page(p, exe, html, "hg38.chrom.sizes")
        try:
            _toolbar_unusable(page)
            assert "Loading viewer" in page.inner_text("#pp-status")
            route, entry = held.pop()
            route.fulfill(body=(render_image.ASSETS / entry[0]).read_bytes(),
                          content_type=entry[1], headers={"Access-Control-Allow-Origin": "*"})
            page.wait_for_function("() => window.igvReady === true || !!window.igvError", timeout=60000)
            assert page.evaluate("document.getElementById('igv-div').inert") is False
            assert page.evaluate("getComputedStyle(document.getElementById('igv-div')).opacity") == "1"
            assert page.inner_text("#pp-status") == "Viewer loaded."          # announced, then visually hidden
            assert page.evaluate("document.getElementById('pp-status').classList.contains('pp-sr-only')")
            assert page.get_attribute("#pp-status", "role") == "status"
            dl = save_via_igv_menu(page, "SVG")
            assert dl.suggested_filename.startswith("IMG_TEST.")
            assert "IMG_TEST" in Path(dl.path()).read_text(encoding="utf-8")
        finally:
            b.close()


def test_viewer_start_failure_is_shown_and_viewer_stays_unusable(viewer):
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b, page, held = _held_viewer_page(p, exe, html, "hg38.chrom.sizes")
        try:
            held.pop()[0].abort()                         # the genome cannot load
            page.wait_for_function("() => window.igvReady === true || !!window.igvError", timeout=60000)
            assert page.evaluate("window.igvError") is not None
            page.wait_for_function(   # the status is written on a short delay (live region priming)
                "() => document.getElementById('pp-status').textContent.includes('could not start')")
            assert "could not start" in page.inner_text("#pp-status")
            _toolbar_unusable(page)
        finally:
            b.close()


def test_viewer_reports_igv_js_failing_to_load(viewer, tmp_path):
    """If igv.js itself does not run, the start-up failure is shown instead of "Loading viewer…"."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    page_html = html.read_text(encoding="utf-8")
    scripts = [m.span() for m in __import__("re").finditer(r"<script>.*?</script>", page_html, __import__("re").S)]
    igv_start, igv_end = scripts[0]                         # the inlined igv.js block
    broken = tmp_path / "noigv.html"
    broken.write_text(page_html[:igv_start] + page_html[igv_end:], encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = b.new_page()
            page.route(lambda u: u.startswith("http"), lambda r: r.abort())
            page.goto(broken.resolve().as_uri())
            page.wait_for_function("() => !!window.igvError", timeout=15000)
            assert "igv is not defined" in page.evaluate("window.igvError")
            page.wait_for_function(   # the status is written on a short delay (live region priming)
                "() => document.getElementById('pp-status').textContent.includes('could not start')")
            assert "could not start" in page.inner_text("#pp-status")
            assert page.evaluate("document.getElementById('igv-div').inert") is True
        finally:
            b.close()


@pytest.mark.parametrize("fail_at", ["new_page", "wait_for_timeout", "close"])
def test_playwright_failure_always_becomes_render_error(monkeypatch, tmp_path, fail_at):
    """A Playwright error, even when the page never opened or has crashed, is reported as
    RenderError (which the CLI prints), not UnboundLocalError or a second Playwright error."""
    pw = pytest.importorskip("playwright.sync_api")

    class Page:
        def __getattr__(self, name):
            if name == "wait_for_timeout" and fail_at == "wait_for_timeout":
                def boom(*a, **k): raise pw.Error("Target closed")
                return boom
            if name == "goto":
                def crash(*a, **k): raise pw.Error("Page crashed")
                return crash
            return lambda *a, **k: None

    class Browser:
        def new_page(self, **k):
            if fail_at == "new_page":
                raise pw.Error("cannot open page")
            return Page()
        def close(self):
            if fail_at == "close":
                raise pw.Error("close failed")

    class Fake:
        chromium = type("C", (), {"launch": staticmethod(lambda **k: Browser())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pw, "sync_playwright", lambda: Fake())
    html = tmp_path / "x.html"
    html.write_text("<html></html>")
    original = {"new_page": "cannot open page", "wait_for_timeout": "Page crashed", "close": "Page crashed"}[fail_at]
    with pytest.raises(render_image.RenderError, match="Rendering failed.*" + original):
        render_image.render_genome_image(html, tmp_path / "x", chromium_path="/nonexistent")


def test_failed_close_after_success_is_a_render_error(monkeypatch, tmp_path):
    pw = pytest.importorskip("playwright.sync_api")

    class Page:
        def evaluate(self, expr, *a):
            return "<svg/>" if "svg()" in expr else ("ready" if "igvReady" in expr else "")
        def __getattr__(self, name):
            return lambda *a, **k: None

    class Browser:
        def new_page(self, **k): return Page()
        def close(self): raise pw.Error("close failed")

    class Fake:
        chromium = type("C", (), {"launch": staticmethod(lambda **k: Browser())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(pw, "sync_playwright", lambda: Fake())
    html = tmp_path / "x.html"
    html.write_text("<html></html>")
    with pytest.raises(render_image.RenderError, match="Could not close Chromium"):
        render_image.render_genome_image(html, tmp_path / "x", chromium_path="/nonexistent")


def test_blocked_viewer_script_leaves_a_visible_message(viewer, tmp_path):
    """If the viewer code cannot run (here: it no longer matches its CSP hash), the static
    fallback line stays visible instead of an empty page."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    tampered = tmp_path / "blocked.html"
    text = html.read_text(encoding="utf-8")
    marker = "// eggd_purple_plotter viewer code."                    # first line of viewer.js
    assert text.count(marker) == 1
    tampered.write_text(text.replace(marker, marker + " (changed)", 1), encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = b.new_page()
            page.route(lambda u: u.startswith("http"), lambda r: r.abort())
            page.goto(tampered.resolve().as_uri())
            page.wait_for_timeout(1500)
            assert page.is_visible("#pp-fallback")
            assert "scripts did not run" in page.inner_text("#pp-fallback")
            assert page.inner_text("#pp-status") == ""
        finally:
            b.close()


def test_status_sequence_loading_then_loaded(viewer):
    """The live region is primed empty, then announces loading and, when ready, completion."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b, page, held = _held_viewer_page(p, exe, html, "hg38.chrom.sizes")
        try:
            assert page.inner_text("#pp-status") == "Loading viewer\u2026"
            assert page.locator("#pp-fallback").count() == 0
            route, entry = held.pop()
            route.fulfill(body=(render_image.ASSETS / entry[0]).read_bytes(),
                          content_type=entry[1], headers={"Access-Control-Allow-Origin": "*"})
            page.wait_for_function("() => window.igvReady === true", timeout=60000)
            page.wait_for_timeout(100)
            assert page.inner_text("#pp-status") == "Viewer loaded."
        finally:
            b.close()


def test_empty_status_region_stays_in_the_accessibility_tree(viewer):
    """The empty live region must be present to assistive technology (not display:none or
    visibility:hidden) so its first message is announced; it is visually hidden while empty.
    Scripts are disabled so the region is guaranteed to still be empty when inspected."""
    exe, html, out = viewer
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = b.new_context(java_script_enabled=False).new_page()
            page.goto(html.resolve().as_uri())
            assert page.inner_text("#pp-status") == ""
            style = page.evaluate("[getComputedStyle(document.getElementById('pp-status')).display,"
                                  " getComputedStyle(document.getElementById('pp-status')).visibility]")
            assert style[0] != "none" and style[1] != "hidden"
            cdp = page.context.new_cdp_session(page)
            nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
            assert any(n.get("role", {}).get("value") == "status" and not n.get("ignored") for n in nodes)
        finally:
            b.close()
