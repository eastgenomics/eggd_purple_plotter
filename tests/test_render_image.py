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


def test_cnvkit_only_viewer_initialises_and_png_button_downloads(viewer):
    """Empty PURPLE tracks are pruned (igv.js aborts on empty SEG), and the Save PNG button works."""
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
            with page.expect_download() as dl_png:
                page.click("#btn-png")
            png_path = Path(dl_png.value.path())
            assert dl_png.value.suggested_filename.startswith("IMG_TEST.") and dl_png.value.suggested_filename.endswith(".png")
            with page.expect_download() as dl_svg:
                page.click("#btn-svg")
            assert dl_svg.value.suggested_filename.endswith(".svg")
            assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            b.close()
