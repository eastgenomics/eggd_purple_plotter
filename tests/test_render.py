import json
import shutil
import sys
import tarfile
from pathlib import Path
import subprocess
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import (PlotInputs, QCData, MSIData, render_html, write_html,
                            load_purple_data, load_cnvkit_data)

FIXTURES = Path(__file__).parent / "test_data"
SCRIPT   = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter/purple_plotter.py"


def test_render_html_contains_sample_id():
    inputs = PlotInputs(sample_id="TEST_SAMPLE")
    html = render_html(inputs)
    assert "TEST_SAMPLE" in html


def test_render_html_is_valid_html():
    inputs = PlotInputs(sample_id="S001")
    html = render_html(inputs)
    assert html.strip().startswith("<!DOCTYPE html>")
    assert "</html>" in html


def test_render_html_cdn_tag():
    inputs = PlotInputs(sample_id="S001")
    html = render_html(inputs)
    assert "igv.org/web/release/3.8.3" in html


def test_render_html_qc_defaults_shown():
    inputs = PlotInputs(sample_id="S001")  # no qc provided → defaults
    html = render_html(inputs)
    assert "?" in html  # purity/ploidy/msi shown as ?


def test_render_html_qc_values_shown():
    inputs = PlotInputs(
        sample_id="S001",
        qc=QCData(purity=0.79, ploidy=2.02, status="NORMAL", loaded=True),
    )
    html = render_html(inputs)
    assert "79%" in html
    assert "2.02" in html
    assert "NORMAL" in html


def test_render_html_qc_loaded_but_unknown_status():
    """Purity/ploidy must display even when QC file has a blank/UNKNOWN status."""
    inputs = PlotInputs(
        sample_id="S001",
        qc=QCData(purity=0.65, ploidy=2.10, status="UNKNOWN", loaded=True),
    )
    html = render_html(inputs)
    assert "65%" in html
    assert "2.10" in html


def test_render_writes_file(tmp_path):
    inputs = PlotInputs(sample_id="S001")
    out = write_html(inputs, tmp_path)
    assert out.exists()
    assert out.name == "S001.igv.bars.html"


# ── CLI end-to-end tests (M5) ─────────────────────────────────────────────────

def _make_tar(tmp_path, sample="sample"):
    tar_path = tmp_path / f"{sample}.purple.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for suffix in ["amber.baf.tsv", "target_region_cn.tsv", "purple.segment.tsv"]:
            tf.add(FIXTURES / f"sample.{suffix}", arcname=f"{sample}.{suffix}")
    return tar_path


def test_cli_purple_only(tmp_path):
    tar = _make_tar(tmp_path)
    r = subprocess.run([
        "python3", str(SCRIPT),
        "--sample", "S001",
        "--purple_tar", str(tar),
        "--qc_report", str(FIXTURES / "sample.qc_report.tsv"),
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = tmp_path / "S001.igv.bars.html"
    assert out.exists()
    assert "S001" in out.read_text()


def test_cli_cnvkit_only(tmp_path):
    r = subprocess.run([
        "python3", str(SCRIPT),
        "--sample", "S002",
        "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "S002.igv.bars.html").exists()


def test_cli_no_data_exits_nonzero(tmp_path):
    r = subprocess.run([
        "python3", str(SCRIPT),
        "--sample", "S003",
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode != 0
    assert "purple_tar" in r.stderr or "cnvkit_cnr" in r.stderr


def test_cli_all_inputs(tmp_path):
    tar = _make_tar(tmp_path)
    r = subprocess.run([
        "python3", str(SCRIPT),
        "--sample", "S004",
        "--purple_tar", str(tar),
        "--qc_report", str(FIXTURES / "sample.qc_report.tsv"),
        "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
        "--cnvkit_call_cns", str(FIXTURES / "sample.call.cns"),
        "--cnvkit_genemetrics", str(FIXTURES / "sample.genemetrics.csv"),
        "--msi_report", str(FIXTURES / "sample.msi.tsv"),
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    html = (tmp_path / "S004.igv.bars.html").read_text()
    assert "79%" in html      # purity
    assert "3.42%" in html    # MSI score



# ── Empty-track pruning: execute the generated JS under node with igv stubbed ──

HARNESS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const texts = {};
let n = 0, cfg = null;
class StubBlob { constructor(parts) { this.t = parts.join(""); } }
const noop = () => ({ addEventListener() {}, appendChild() {}, remove() {}, click() {} });
const ctx = {
  Blob: StubBlob,
  URL: { createObjectURL(b) { const u = "blob:" + (n++); texts[u] = b.t; return u; }, revokeObjectURL() {} },
  document: { getElementById: noop, createElement: noop, body: noop() },
  igv: { createBrowser(_el, c) { cfg = c; return Promise.resolve({}); } },
  location: { search: "" }, URLSearchParams,
  console, setTimeout,
};
ctx.window = ctx;
require("vm").runInNewContext(scripts.join("\n"), ctx);
const flat = (t) => (t.tracks || [t]).map(c => ({ empty: !texts[c.url].trim() }));
const nonempty = Object.values(texts).filter(t => t.trim()).length;
const segSamples = Object.values(texts).filter(t => t.startsWith("SampleID\t"))
  .map(t => t.split("\n")[1].split("\t")[0]);
process.stdout.write(JSON.stringify({
  nonempty,
  injected: ctx.INJECTED === undefined ? null : String(ctx.INJECTED),
  segSamples,
  tracks: cfg.tracks.map(t => ({ name: t.name, children: flat(t) })),
}));
"""


def _tracks_for(tmp_path, **kw):
    """Render, run the page script under node with igv stubbed.

    Returns [{name, children:[{empty}]}]. Also asserts every non-empty data blob
    ends up in exactly one surviving track/child (nothing populated is dropped).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    kw.setdefault("sample_id", "S001")
    out = tmp_path / "page.html"        # fixed name: sample ids may contain '/' etc.
    out.write_text(render_html(PlotInputs(**kw)))
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    res = subprocess.run([node, str(harness), str(out)], capture_output=True, text=True, check=True)  # nosec B603
    parsed = json.loads(res.stdout)
    _tracks_for.last = parsed
    kept = sum(len(t["children"]) for t in parsed["tracks"])
    assert kept == parsed["nonempty"], f"{kept} tracks kept but {parsed['nonempty']} blobs have data"
    return parsed["tracks"]


def _purple(tmp_path):
    tar = tmp_path / "S001.purple.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for f in ("sample.amber.baf.tsv", "sample.purple.segment.tsv", "sample.target_region_cn.tsv"):
            tf.add(FIXTURES / f, arcname=f)
    return load_purple_data(tar)


def _assert_no_empty_tracks(tracks):
    for t in tracks:
        assert t["children"], f"track {t['name']!r} has no data children"
        assert not any(c["empty"] for c in t["children"]), f"track {t['name']!r} still has an empty-data track"


def test_no_inputs_yields_no_tracks(tmp_path):
    assert _tracks_for(tmp_path) == []


def test_cnvkit_only_drops_purple_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, cnvkit=load_cnvkit_data(
        FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", FIXTURES / "sample.genemetrics.csv"))
    _assert_no_empty_tracks(tracks)
    names = [t["name"] for t in tracks]
    assert names == ["CNVkit log\u2082", "CNVkit segments", "CNVkit genemetrics"]


def test_purple_only_drops_cnvkit_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, purple=_purple(tmp_path))
    _assert_no_empty_tracks(tracks)
    names = [t["name"] for t in tracks]
    assert names and not any(n.startswith("CNVkit") for n in names)
    assert "Coverage + CN (log\u2082)" in names


def test_full_inputs_keep_all_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, purple=_purple(tmp_path), cnvkit=load_cnvkit_data(
        FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", FIXTURES / "sample.genemetrics.csv"))
    _assert_no_empty_tracks(tracks)
    assert len(tracks) == 7
    assert sum(len(t["children"]) for t in tracks) > 7   # merged tracks keep their several children


def test_render_html_has_export_buttons_and_hooks():
    html = render_html(PlotInputs(sample_id="S001"))
    assert 'id="btn-svg"' in html and 'id="btn-png"' in html
    assert "window.ppExport" in html
    assert "window.igvReady" in html


def test_render_html_export_filename_is_sanitised():
    html = render_html(PlotInputs(sample_id='a b/"c`${x}</script>'))
    assert 'const ppSampleFile = "a_b_c_x_script_";' in html


@pytest.mark.parametrize("sid", [
    "A\\${globalThis.INJECTED=123}\\`B",
    "A</script><script>globalThis.INJECTED=1</script>",
    "A<!--<script>B",
    "A\u2028B",
])
def test_sample_id_cannot_break_out_of_js_template_literal(tmp_path, sid):
    """Backslash/`${`/script-parser tricks in sample_id must stay inert and round-trip into SEG data."""
    cn = load_cnvkit_data(FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", None)
    _tracks_for(tmp_path, sample_id=sid, purple=_purple(tmp_path), cnvkit=cn)
    out = _tracks_for.last
    assert out["injected"] is None
    assert len(out["segSamples"]) >= 2 and all(s.removesuffix("_cnvkit") == sid for s in out["segSamples"])
    assert "<!--" not in (tmp_path / "page.html").read_text().split("<script>", 2)[-1]
