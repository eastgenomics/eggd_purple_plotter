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
  console, setTimeout,
};
ctx.window = ctx;
require("vm").runInNewContext(scripts.join("\n"), ctx);
// Independent of the viewer's own check: drop a first line that is the SEG column header.
const rows = (t) => { const ls = t.split("\n"); if (ls[0] === "SampleID\tChromosome\tStart\tEnd\tNum_Probes\tSeg_Mean") ls.shift();
                      return ls.some(l => l.trim()); };
const flat = (t) => (t.tracks || [t]).map(c => ({ empty: !rows(texts[c.url]) }));
const nonempty = Object.values(texts).filter(rows).length;
process.stdout.write(JSON.stringify({
  nonempty,
  tracks: cfg.tracks.map(t => ({ name: t.name, height: t.height, children: flat(t) })),
}));
"""


def _tracks_for(tmp_path, sample_id="S001", **kw):
    """Render, run the page script under node with igv stubbed.

    Returns [{name, children:[{empty}]}] ("empty" = no data rows). Also asserts every data blob
    with rows ends up in exactly one surviving track/child (nothing populated is dropped).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    out = write_html(PlotInputs(sample_id=sample_id, **kw), tmp_path)
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    res = subprocess.run([node, str(harness), str(out)], capture_output=True, text=True, check=True)  # nosec B603
    parsed = json.loads(res.stdout)
    kept = sum(1 for t in parsed["tracks"] for c in t["children"] if not c["empty"])
    assert kept == parsed["nonempty"], f"{kept} populated tracks kept but {parsed['nonempty']} blobs have data"
    return parsed["tracks"]


def _purple(tmp_path):
    tar = tmp_path / "S001.purple.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for f in ("sample.amber.baf.tsv", "sample.purple.segment.tsv", "sample.target_region_cn.tsv"):
            tf.add(FIXTURES / f, arcname=f)
    return load_purple_data(tar)


def _assert_tracks_have_data(tracks):
    """Every surviving track has data, or is labelled "(no data)" (its input was supplied)."""
    for t in tracks:
        assert t["children"], f"track {t['name']!r} has no children"
        if all(c["empty"] for c in t["children"]):
            assert t["name"].endswith("(no data)"), f"empty track {t['name']!r} is not labelled"


def test_no_inputs_yields_no_tracks(tmp_path):
    assert _tracks_for(tmp_path) == []


def test_cnvkit_only_drops_purple_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, cnvkit=load_cnvkit_data(
        FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", FIXTURES / "sample.genemetrics.csv"))
    _assert_tracks_have_data(tracks)
    names = [t["name"] for t in tracks]
    assert names == ["CNVkit log\u2082", "CNVkit segments", "CNVkit genemetrics"]


def test_purple_only_drops_cnvkit_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, purple=_purple(tmp_path))
    _assert_tracks_have_data(tracks)
    names = [t["name"] for t in tracks]
    assert names and not any(n.startswith("CNVkit") for n in names)
    assert "Coverage + CN (log\u2082)" in names


def test_full_inputs_keep_all_tracks(tmp_path):
    tracks = _tracks_for(tmp_path, purple=_purple(tmp_path), cnvkit=load_cnvkit_data(
        FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", FIXTURES / "sample.genemetrics.csv"))
    _assert_tracks_have_data(tracks)
    assert len(tracks) == 7
    assert sum(len(t["children"]) for t in tracks) > 7   # merged tracks keep their several children


def test_supplied_inputs_keep_their_empty_children(tmp_path):
    """Children with no rows (e.g. no amplifications in this sample) stay when PURPLE was
    supplied: absence of data is shown, not hidden."""
    tracks = {t["name"]: t for t in _tracks_for(tmp_path, purple=_purple(tmp_path))}
    cov = tracks["Coverage + CN (log\u2082)"]["children"]
    assert len(cov) == 5 and any(c["empty"] for c in cov)      # amp/homdel bars empty in the fixture
    assert len(tracks["BAF"]["children"]) == 10


def test_supplied_input_with_no_rows_is_labelled_not_removed(tmp_path):
    """A genemetrics file with a header but no genes is not the same as no genemetrics input."""
    gm = tmp_path / "empty.genemetrics.csv"
    gm.write_text((FIXTURES / "sample.genemetrics.csv").read_text().splitlines()[0] + "\n")
    cn = load_cnvkit_data(FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", gm)
    names = [t["name"] for t in _tracks_for(tmp_path, cnvkit=cn)]
    assert names == ["CNVkit log\u2082", "CNVkit segments", "CNVkit genemetrics (no data)"]
    absent = [t["name"] for t in _tracks_for(tmp_path, cnvkit=load_cnvkit_data(
        FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", None))]
    assert absent == ["CNVkit log\u2082", "CNVkit segments"]             # not supplied: removed


def test_supplied_segments_with_no_rows_are_labelled(tmp_path):
    """A called-segments file with no usable rows gives a header-only SEG (igv.js accepts it),
    shown as "CNVkit segments (no data)"."""
    import pandas as pd
    cns = pd.DataFrame({"chromosome": ["chrUn"], "start": [1], "end": [2], "log2": [0.1],
                        "cn": [2], "probes": [1]})                     # only non-reported chromosomes
    from purple_plotter import CNVkitData
    cn = load_cnvkit_data(FIXTURES / "sample.cnr", None, None)
    names = [t["name"] for t in _tracks_for(tmp_path, cnvkit=CNVkitData(cnr_df=cn.cnr_df, cns_df=cns))]
    assert names == ["CNVkit log\u2082", "CNVkit segments (no data)"]


def test_sample_named_like_the_seg_header_is_not_no_data(tmp_path):
    """Only the first SEG line is a header: a sample literally called "SampleID" still has rows."""
    tracks = _tracks_for(tmp_path, sample_id="SampleID", purple=_purple(tmp_path))
    assert not any(t["name"].endswith("(no data)") for t in tracks)
    assert [t["name"] for t in tracks].count(" ") == 2                 # both PURPLE segment strips


def test_no_data_segment_tracks_are_tall_enough_for_their_label(tmp_path):
    """PURPLE segment strips are 10 px; when PURPLE was supplied but has no usable segments
    (all copy numbers <= 0) they are labelled "(no data)" and raised to fit the label."""
    import pandas as pd
    from purple_plotter import PurpleData
    purple = _purple(tmp_path)
    seg0 = purple.seg_df.assign(tumorCopyNumber=0.0)               # to_seg skips CN <= 0
    tracks = _tracks_for(tmp_path, purple=PurpleData(baf_df=purple.baf_df, cn_df=purple.cn_df, seg_df=seg0))
    strips = [t for t in tracks if t["name"] == "Segments (no data)"]
    assert len(strips) == 2 and all(t["height"] >= 25 for t in strips)


def test_supplied_cnvkit_file_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_cnvkit_data(tmp_path / "nope.cnr", None, None)
    r = subprocess.run([
        sys.executable, str(SCRIPT), "--sample", "X", "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
        "--cnvkit_genemetrics", str(tmp_path / "missing.genemetrics.csv"), "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)  # nosec B603 -- our own CLI with fixed arguments
    assert r.returncode != 0 and "does not exist" in r.stderr and "Traceback" not in r.stderr
    assert not (tmp_path / "X.igv.bars.html").exists()


@pytest.mark.parametrize("args", [
    ["--cnvkit_cnr", ""],                                                        # the required input
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--cnvkit_call_cns", ""],     # optional ones
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--cnvkit_genemetrics", ""],
    ["--purple_tar", ""],
])
def test_empty_file_argument_is_an_error_not_omitted(tmp_path, args):
    r = subprocess.run([sys.executable, str(SCRIPT), "--sample", "E", *args, "--output_dir", str(tmp_path)],
                       capture_output=True, text=True)  # nosec B603 -- our own CLI with fixed arguments
    assert r.returncode != 0 and "does not exist" in r.stderr and "Traceback" not in r.stderr
    assert not (tmp_path / "E.igv.bars.html").exists()
