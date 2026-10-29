"""Atlas v2.0.0 inputs: PURPLE purity TSV as qc_report, fit status, ploidy fallback, genemetrics TSV."""
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

PKG = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"
sys.path.insert(0, str(PKG))

from purple_plotter import (CNVkitData, InputError, PlotInputs, PurpleData, QCData,
                            load_cnvkit_data, load_qc_data, render_html)

FIXTURES = Path(__file__).parent / "test_data"
SCRIPT = PKG / "purple_plotter.py"
DXAPP = Path(__file__).parent.parent / "dxapp.json"
PURITY_TSV = FIXTURES / "sample.purple.purity.tsv"          # purity 0.31, ploidy 3.9, NORMAL


def _data(html):
    return json.loads(re.search(r'<script type="application/json" id="pp-data">(.*?)</script>',
                                html, re.S).group(1))


def _header(html):
    return re.search(r'<div id="hdr">(.*?)<span class="btns">', html, re.S).group(1)


def _cli(tmp_path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--output_dir", str(tmp_path), *args],
                          capture_output=True, text=True)  # nosec B603


def _purple_with_cn(cn):
    seg = pd.DataFrame({"chromosome": ["chr2"], "start": [1], "end": [100000],
                        "tumorCopyNumber": [cn], "tumorBAF": [0.5]})
    empty = pd.DataFrame({"chromosome": [], "position": [], "tumorBAF": [], "normalBAF": []})
    cn_df = pd.DataFrame({"chromosome": [], "windowStart": [], "windowEnd": [], "windowTumorRatio": [],
                          "masked": []})
    return PurpleData(baf_df=empty, cn_df=cn_df, seg_df=seg)


def _cnvkit_with_cn(cn):
    cns = pd.DataFrame({"chromosome": ["chr2"], "start": [1], "end": [200000], "log2": [0.5],
                        "cn": [cn], "probes": [12]})
    return CNVkitData(cns_df=cns)


# ── PURPLE purity TSV through qc_report ────────────────────────────────────────

def test_purple_purity_tsv_parses():
    qc = load_qc_data(PURITY_TSV)
    assert qc.loaded and qc.purity == pytest.approx(0.31) and qc.ploidy == pytest.approx(3.9)
    assert qc.status == "NORMAL"


def test_purple_purity_tsv_through_cli_shows_fit_status_not_qc(tmp_path):
    r = _cli(tmp_path, "--sample", "P1", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
             "--qc_report", str(PURITY_TSV))
    assert r.returncode == 0, r.stderr
    html = (tmp_path / "P1.igv.bars.html").read_text(encoding="utf-8")
    hdr = _header(html)
    assert "Purity 31%" in hdr and "Ploidy 3.90" in hdr
    assert "Fit status: NORMAL" in hdr and "not a QC result" in hdr
    assert "PASS" not in hdr and 'class="warn"' not in hdr


def test_extra_columns_are_ignored(tmp_path):
    p = tmp_path / "wide.purity.tsv"
    p.write_text("purity\tnormFactor\tscore\tploidy\tgender\tstatus\tmsStatus\n"
                 "0.62\t1.01\t0.3\t2.45\tFEMALE\tNO_TUMOR\tMSS\n")
    qc = load_qc_data(p)
    assert (qc.purity, qc.ploidy, qc.status) == (pytest.approx(0.62), pytest.approx(2.45), "NO_TUMOR")
    hdr = _header(render_html(PlotInputs(sample_id="W", qc=qc)))
    assert "Fit status: NO_TUMOR" in hdr and "Ploidy 2.45" in hdr


@pytest.mark.parametrize("content", ["purity\tploidy\tstatus\n0.4\t2.2\t\n", "purity\tploidy\n0.4\t2.2\n"])
def test_blank_or_absent_status_is_unknown_not_absent_report(tmp_path, content):
    p = tmp_path / "s.tsv"
    p.write_text(content)
    qc = load_qc_data(p)
    assert qc.loaded and qc.status == "UNKNOWN"
    hdr = _header(render_html(PlotInputs(sample_id="S", qc=qc)))
    assert "Fit status: UNKNOWN" in hdr and "Purity 40%" in hdr and 'class="warn"' not in hdr


@pytest.mark.parametrize("content, msg", [
    ("ploidy\tstatus\n2.0\tNORMAL\n", "no purity column"),
    ("purity\tstatus\n0.5\tNORMAL\n", "no ploidy column"),
    ("purity\tploidy\n0.5\tabc\n", "ploidy value 'abc' is not a number"),
    ("purity\tploidy\nNA\t2.0\n", "purity value"),
    ("purity\tploidy\n", "no data row"),
    ("", "is empty"),
    ("purity,ploidy,status\n0.5,2.0,NORMAL\n", "no purity/ploidy column"),     # comma-separated
])
def test_malformed_report_fails_clearly(tmp_path, content, msg):
    p = tmp_path / "bad.tsv"
    p.write_text(content)
    with pytest.raises(InputError, match=re.escape(msg)):
        load_qc_data(p)
    r = _cli(tmp_path, "--sample", "B", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--qc_report", str(p))
    assert r.returncode != 0 and msg in r.stderr
    assert not (tmp_path / "B.igv.bars.html").exists()


def test_supplied_but_missing_report_is_an_error(tmp_path):
    with pytest.raises(InputError, match="does not exist"):
        load_qc_data(tmp_path / "nope.tsv")


# ── Fractional ploidy and the absent-report fallback ──────────────────────────

def test_fractional_ploidy_is_the_denominator():
    html = render_html(PlotInputs(sample_id="F", purple=_purple_with_cn(3.9), cnvkit=_cnvkit_with_cn(4),
                                  qc=QCData(purity=0.31, ploidy=3.9, status="NORMAL", loaded=True)))
    d = _data(html)
    assert d["seg_data"].split("\n")[1].split("\t")[-1] == "0.0000"                       # log2(3.9/3.9)
    assert d["cnvkit_sns_bg"].split("\n")[1].split("\t")[-1] == f"{math.log2(4 / 3.9):.4f}"
    assert f"{math.log2(3.9 / 2):.4f}" not in d["seg_data"]


def test_absent_report_assumes_ploidy_2_and_says_so():
    html = render_html(PlotInputs(sample_id="A", purple=_purple_with_cn(3.9), cnvkit=_cnvkit_with_cn(4)))
    d, hdr = _data(html), _header(html)
    assert d["seg_data"].split("\n")[1].split("\t")[-1] == f"{math.log2(3.9 / 2):.4f}"
    assert d["cnvkit_sns_bg"].split("\n")[1].split("\t")[-1] == f"{math.log2(4 / 2):.4f}"
    assert "Purity ?" in hdr and "Ploidy ?" in hdr and "Fit status: ?" in hdr
    assert 'class="warn"' in hdr and "ploidy 2.0 is assumed" in hdr and "not a measured ploidy" in hdr


# ── Genemetrics TSV ───────────────────────────────────────────────────────────

def test_dxapp_accepts_genemetrics_tsv_and_legacy_csv():
    spec = {i["name"]: i for i in json.loads(DXAPP.read_text())["inputSpec"]}
    pats = spec["cnvkit_genemetrics"]["patterns"]
    for p in ("*.genemetrics.tsv", "*.tsv", "*.genemetrics.csv", "*.csv"):
        assert p in pats
    assert "tab-separated" in spec["cnvkit_genemetrics"]["help"]
    assert spec["qc_report"]["name"] == "qc_report" and spec["qc_report"].get("optional") is True


@pytest.mark.parametrize("with_purple", [False, True])
def test_genemetrics_tsv_through_cli(tmp_path, with_purple):
    args = ["--sample", "G", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
            "--cnvkit_call_cns", str(FIXTURES / "sample.call.cns"),
            "--cnvkit_genemetrics", str(FIXTURES / "sample.genemetrics.tsv")]
    if with_purple:
        import tarfile
        tar = tmp_path / "G.purple.tar.gz"
        with tarfile.open(tar, "w:gz") as tf:
            for f in ("sample.amber.baf.tsv", "sample.purple.segment.tsv", "sample.target_region_cn.tsv"):
                tf.add(FIXTURES / f, arcname=f)
        args += ["--purple_tar", str(tar), "--qc_report", str(PURITY_TSV)]
    r = _cli(tmp_path, *args)
    assert r.returncode == 0, r.stderr
    gm = _data((tmp_path / "G.igv.bars.html").read_text(encoding="utf-8"))["cnvkit_gm_bg"]
    assert gm.split("\n") == ["chr3\t100\t900\t0.6789"]


def test_legacy_genemetrics_csv_still_read_as_tsv():
    d = load_cnvkit_data(None, None, FIXTURES / "sample.genemetrics.csv")
    assert list(d.gm_df["gene"][:2]) == ["GENE1", "GENE2"]


# ── Security ──────────────────────────────────────────────────────────────────

def test_fit_status_markup_is_text_and_scripts_unchanged():
    import html as _h
    evil = QCData(purity=0.5, ploidy=2.0, status='<img src=x onerror=alert(1)>"', loaded=True)
    a = render_html(PlotInputs(sample_id="S", qc=evil))
    b = render_html(PlotInputs(sample_id="S"))
    assert "<img src=x" not in a and "Fit status: " + _h.escape(evil.status, quote=True) in a
    scripts = lambda h: re.findall(r"<script>(.*?)</script>", h, re.S)
    assert scripts(a) == scripts(b)


def test_exported_image_carries_the_ploidy_warning(tmp_path):
    pytest.importorskip("playwright.sync_api")
    import shutil, os
    exe = os.environ.get("CHROMIUM_PATH") or next(
        (shutil.which(e) for e in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
         if shutil.which(e)), None)
    if exe is None:
        pytest.skip("no Chromium available")
    import render_image
    html = tmp_path / "w.html"
    html.write_text(render_html(PlotInputs(
        sample_id="WARN", cnvkit=load_cnvkit_data(FIXTURES / "sample.cnr", None, None))), encoding="utf-8")
    svg, _ = render_image.render_genome_image(html, tmp_path / "w", chromium_path=exe)
    text = svg.read_text()
    assert "ploidy 2.0 is assumed" in text and "Fit status: ?" in text


@pytest.mark.parametrize("content, msg", [
    ("purity\tploidy\n0.31\t0.8\t3.9\n", "first data row has 3 fields but the header has 2"),
    ("purity\tploidy\tstatus\n0.31\t3.9\n", "first data row has 2 fields but the header has 3"),
    ('purity\tploidy\tstatus\n0.3\t2.0\t"unterminated\n', None),            # quotes are plain text
    ("purity\tploidy\n1.5\t2.0\n", "purity 1.5 is outside 0-1"),
    ("purity\tploidy\n0.5\t0\n", "ploidy 0.0 is below 0.1"),
    ("purity\tploidy\n0.5\t-2\n", "ploidy -2.0 is below 0.1"),
])
def test_report_row_shape_and_ranges(tmp_path, content, msg):
    p = tmp_path / "r.tsv"
    p.write_text(content)
    if msg is None:
        assert load_qc_data(p).status == '"unterminated'
        return
    with pytest.raises(InputError, match=re.escape(msg)):
        load_qc_data(p)


def test_cli_empty_qc_report_argument_is_an_error_not_absent(tmp_path):
    r = _cli(tmp_path, "--sample", "E", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--qc_report", "")
    assert r.returncode != 0 and "does not exist" in r.stderr and "Traceback" not in r.stderr


def test_unreadable_report_is_an_input_error(tmp_path):
    p = tmp_path / "bin.tsv"
    p.write_bytes(b"purity\tploidy\n\xff\xfe\n")
    with pytest.raises(InputError, match="cannot be read"):
        load_qc_data(p)


def test_legend_does_not_call_zero_diploid():
    html = render_html(PlotInputs(sample_id="L"))
    assert "log&#x2082;(CN/ploidy); 0&#xa0;=&#xa0;CN equal to the ploidy" in html
    assert "log&#x2082;(CN/ploidy); 0&#xa0;=&#xa0;diploid" not in html


@pytest.mark.parametrize("content, col", [
    ("purity\tploidy\tploidy\n0.3\t3.9\t2.0\n", "ploidy"),
    ("purity\tpurity\tploidy\n0.3\t0.5\t2.0\n", "purity"),
    ("purity\tploidy\tstatus\tstatus\n0.3\t2.0\tNORMAL\tNO_TUMOR\n", "status"),
])
def test_duplicate_columns_are_rejected(tmp_path, content, col):
    p = tmp_path / "d.tsv"
    p.write_text(content)
    with pytest.raises(InputError, match=f"more than one {col} column"):
        load_qc_data(p)


def test_bom_prefixed_report_is_accepted(tmp_path):
    p = tmp_path / "bom.tsv"
    p.write_bytes(b"\xef\xbb\xbf" + PURITY_TSV.read_bytes())
    qc = load_qc_data(p)
    assert (qc.purity, qc.ploidy, qc.status) == (pytest.approx(0.31), pytest.approx(3.9), "NORMAL")
    r = _cli(tmp_path, "--sample", "BOM", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--qc_report", str(p))
    assert r.returncode == 0, r.stderr
    assert "Ploidy 3.90" in (tmp_path / "BOM.igv.bars.html").read_text(encoding="utf-8")
