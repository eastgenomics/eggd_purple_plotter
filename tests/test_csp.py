"""Content-Security-Policy in a real browser: no violations across the viewer's actions.

A <meta> CSP has no report-only mode, so violations are collected with a
`securitypolicyviolation` listener. `test_csp_offline` uses the renderer's offline routing and
runs in CI. `test_csp_live` loads the real igv.org / UCSC resources and only runs with
PP_LIVE_NETWORK=1 (it also exercises gene search, base-level sequence and the RefSeq track).
Skipped without Playwright and a Chromium (see test_render_image.py).
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"
sys.path.insert(0, str(PKG))

import render_image
from purple_plotter import PlotInputs, QCData, load_cnvkit_data, load_purple_data, write_html

FIXTURES = Path(__file__).parent / "test_data"

INIT = """
window.__csp = [];
document.addEventListener("securitypolicyviolation",
  e => window.__csp.push(e.violatedDirective + " " + e.blockedURI));
"""


def _chromium():
    p = os.environ.get("CHROMIUM_PATH")
    if p:
        return p
    for exe in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(exe):
            return shutil.which(exe)
    return None


@pytest.fixture(scope="module")
def full_html(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    exe = _chromium()
    if exe is None:
        pytest.skip("no Chromium available (set CHROMIUM_PATH)")
    import tarfile
    out = tmp_path_factory.mktemp("csp")
    tar = out / "CSP.purple.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for f in ("sample.amber.baf.tsv", "sample.purple.segment.tsv", "sample.target_region_cn.tsv"):
            tf.add(FIXTURES / f, arcname=f)
    html = write_html(PlotInputs(
        sample_id="CSP_TEST",
        purple=load_purple_data(tar),
        qc=QCData(purity=0.79, ploidy=2.02, status="NORMAL", loaded=True),
        cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns",
                                FIXTURES / "sample.genemetrics.csv"),
    ), out)
    return exe, html


def _exercise(exe, html, offline, loci):
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
            page = b.new_page(viewport={"width": 1600, "height": 1000})
            page.add_init_script(INIT)
            errors, requests = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: requests.append(r.url))
            if offline:
                page.route(lambda u: u.startswith("http"), handle)
            page.goto(html.resolve().as_uri())
            page.wait_for_function("() => window.igvReady === true || !!window.igvError", timeout=60000)
            assert page.evaluate("window.igvError || null") is None
            for locus, expect, wait_ms in loci:
                page.evaluate("l => window.igvBrowser.search(l)", locus)
                page.wait_for_timeout(wait_ms)
                shown = page.evaluate("[].concat(window.igvBrowser.currentLoci())")
                assert any(l.startswith(expect) for l in shown), (locus, shown)   # the search really moved
            assert page.evaluate("window.ppExport.svg()").startswith("<svg")
            assert len(page.evaluate("s => window.ppExport.png(s)", 2)) > 1000
            for btn in ("#btn-svg", "#btn-png"):        # the real click path (inline handlers would violate)
                with page.expect_download():
                    page.click(btn)
            return page.evaluate("window.__csp"), errors, requests
        finally:
            b.close()


def test_csp_offline(full_html):
    exe, html = full_html
    violations, errors, _ = _exercise(exe, html, offline=True, loci=[
        ("all", "all", 500), ("chr7", "chr7", 800),
        ("chr7:55019017-55021017", "chr7:55019017-55021017", 800)])
    assert violations == []
    assert errors == []


@pytest.mark.skipif(os.environ.get("PP_LIVE_NETWORK") != "1", reason="set PP_LIVE_NETWORK=1 to use igv.org/UCSC")
def test_csp_live(full_html):
    exe, html = full_html
    violations, errors, requests = _exercise(exe, html, offline=False, loci=[
        ("all", "all", 2000),
        ("EGFR", "chr7:55", 5000),                                     # gene search (igv.org MANE index)
        ("chr7:55019017-55019117", "chr7:55019017-55019117", 5000),    # base level: sequence
    ])
    assert violations == []
    assert errors == []
    assert any(u.startswith("https://hgdownload.soe.ucsc.edu/") and u.endswith("hg38.2bit") for u in requests)
    assert any("ncbiRefSeq" in u for u in requests)                    # RefSeq track loaded
