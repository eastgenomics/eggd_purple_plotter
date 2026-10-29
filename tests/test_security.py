"""Inlined igv.js, data/code split and the Content-Security-Policy meta (no browser needed)."""
import base64
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"
sys.path.insert(0, str(PKG))

import purple_plotter as pp
from purple_plotter import PlotInputs, QCData, load_cnvkit_data, load_purple_data, render_html

FIXTURES = Path(__file__).parent / "test_data"
SCRIPT = PKG / "purple_plotter.py"

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)          # executable (untyped) scripts only
CSP_RE = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">')


def _purple(tmp_path):
    tar = tmp_path / "S.purple.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for f in ("sample.amber.baf.tsv", "sample.purple.segment.tsv", "sample.target_region_cn.tsv"):
            tf.add(FIXTURES / f, arcname=f)
    return load_purple_data(tar)


def _csp(html):
    m = CSP_RE.search(html)
    assert m, "no CSP meta"
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in m.group(1).split(";")) if d}


def _sha256_b64(text):
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def test_igv_inlined_and_verified():
    html = render_html(PlotInputs(sample_id="S001"))
    scripts = SCRIPT_RE.findall(html)
    assert scripts[0] == (PKG / "assets" / "igv.min.js").read_text(encoding="utf-8")
    assert "Permission is hereby granted" in html and "Broad Institute" in html     # MIT notice kept


def test_wrong_igv_asset_fails_at_load(monkeypatch, tmp_path):
    (tmp_path / "igv.min.js").write_text("console.log('not igv')")
    monkeypatch.setattr(pp, "ASSETS", tmp_path)
    pp.igv_js.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="SHA-256"):
            pp.igv_js()
    finally:
        pp.igv_js.cache_clear()


@pytest.mark.parametrize("bad", ["</script>", "</SCRIPT >", "<Script>", "<!--"])
def test_inline_guard_rejects_parser_breaking_text(bad):
    with pytest.raises(RuntimeError):
        pp._guard_inline_script("x.js", f"var a = 1; /* {bad} */")


def test_viewer_code_identical_across_samples(tmp_path):
    a = render_html(PlotInputs(sample_id="S001"))
    b = render_html(PlotInputs(
        sample_id="25330S0047-other</b>",
        locus="chr7:1-1000",
        purple=_purple(tmp_path),
        qc=QCData(purity=0.5, ploidy=3.1, status="NORMAL", loaded=True),
        cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns",
                                FIXTURES / "sample.genemetrics.csv"),
    ))
    assert SCRIPT_RE.findall(a) == SCRIPT_RE.findall(b)
    assert _csp(a)["script-src"] == _csp(b)["script-src"]


def test_csp_meta_first_and_hashes_match():
    html = render_html(PlotInputs(sample_id="S001"))
    head = html[: html.index("</head>")]
    assert head.index('<meta charset="UTF-8">') < head.index("Content-Security-Policy")
    assert html.index("Content-Security-Policy") < html.index("<script")
    csp = _csp(html)
    want = [f"'{_sha256_b64(s)}'" for s in SCRIPT_RE.findall(html)]
    assert len(want) == 2 and csp["script-src"] == want
    assert not any(k in " ".join(csp["script-src"]) for k in ("unsafe-inline", "unsafe-eval", "*"))
    assert csp["default-src"] == ["'none'"]
    assert csp["connect-src"] == ["blob:", "https://igv.org", "https://hgdownload.soe.ucsc.edu"]
    assert '"' not in pp.content_security_policy()


def test_json_block_encoding():
    obj = {"s": "a</script><!--<b>&c\u2028d\u2029e`${x}\\", "n": [1, 2]}
    text = pp.json_for_html(obj)
    for ch in ("<", ">", "&", "\u2028", "\u2029"):
        assert ch not in text
    assert all(ord(c) < 128 for c in text)
    assert json.loads(text) == obj


def test_cli_print_script_hashes(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--print-script-hashes"],
                       capture_output=True, text=True, check=True)  # nosec B603
    hashes = r.stdout.split()
    assert len(hashes) == 2 and all(h.startswith("sha256-") for h in hashes)
    html = render_html(PlotInputs(sample_id="S001"))
    assert _csp(html)["script-src"] == [f"'{h}'" for h in hashes]


def test_cli_still_requires_sample(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--cnvkit_cnr", str(FIXTURES / "sample.cnr")],
                       capture_output=True, text=True)  # nosec B603
    assert r.returncode != 0 and "--sample" in r.stderr
