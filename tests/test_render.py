import json
import shutil
import sys
import tarfile
from pathlib import Path
import subprocess
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import (PlotInputs, QCData, render_html, write_html,
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


def test_no_remote_script():
    html = render_html(PlotInputs(sample_id="S001"))
    assert "<script src=" not in html
    assert "igv.org/web/release" not in html


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
        sys.executable, str(SCRIPT),
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
        sys.executable, str(SCRIPT),
        "--sample", "S002",
        "--cnvkit_cnr", str(FIXTURES / "sample.cnr"),
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "S002.igv.bars.html").exists()


def test_cli_no_data_exits_nonzero(tmp_path):
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--sample", "S003",
        "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)
    assert r.returncode != 0
    assert "purple_tar" in r.stderr or "cnvkit_cnr" in r.stderr


def test_cli_all_inputs(tmp_path):
    tar = _make_tar(tmp_path)
    r = subprocess.run([
        sys.executable, str(SCRIPT),
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
// Run only the viewer code (the last plain <script>); igv.js itself is stubbed below.
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const viewer = scripts[scripts.length - 1];
const dataJson = html.match(/<script type="application\/json" id="pp-data">([\s\S]*?)<\/script>/)[1];
const texts = {};
let n = 0, cfg = null;
class StubBlob { constructor(parts) { this.t = parts.join(""); } }
const noop = () => ({ addEventListener() {}, appendChild() {}, remove() {}, click() {}, classList: { toggle() {} }, style: {} });
const ctx = {
  Blob: StubBlob,
  URL: { createObjectURL(b) { const u = "blob:" + (n++); texts[u] = b.t; return u; }, revokeObjectURL() {} },
  document: {
    getElementById: (id) => id === "pp-data" ? { textContent: dataJson } : noop(),
    createElement: noop, body: noop(),
  },
  igv: { createBrowser(_el, c) { cfg = c; return Promise.resolve({}); } },
  location: { search: "" }, URLSearchParams,
  console, setTimeout,
};
ctx.window = ctx;
require("vm").runInNewContext(viewer, ctx);
// Independent of the viewer's own check: drop a first line that is the SEG column header.
const rows = (t) => { const ls = t.split("\n"); if (ls[0] === "SampleID\tChromosome\tStart\tEnd\tNum_Probes\tSeg_Mean") ls.shift();
                      return ls.some(l => l.trim()); };
const flat = (t) => (t.tracks || [t]).map(c => ({ empty: !rows(texts[c.url]) }));
const nonempty = Object.values(texts).filter(rows).length;
const segSamples = Object.values(texts).filter(t => t.startsWith("SampleID\t"))
  .map(t => t.split("\n")[1]).filter(Boolean).map(l => l.split("\t")[0]);
process.stdout.write(JSON.stringify({
  nonempty,
  injected: ctx.INJECTED === undefined ? null : String(ctx.INJECTED),
  segSamples,
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
    kw["sample_id"] = sample_id
    out = tmp_path / "page.html"        # fixed name: sample ids may contain '/' etc.
    out.write_text(render_html(PlotInputs(**kw)))
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    res = subprocess.run([node, str(harness), str(out)], capture_output=True, text=True, check=True)  # nosec B603
    parsed = json.loads(res.stdout)
    _tracks_for.last = parsed
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


def test_render_html_has_no_extra_save_buttons_and_hooks_igv_save_menu():
    html = render_html(PlotInputs(sample_id="S001"))
    assert "<button" not in html.split("<script")[0]              # no extra buttons: igv.js's Save Image menu is used
    assert "b.saveSVGtoFile = ppSaveSvg" in html and "b.savePNGtoFile = ppSavePng" in html
    assert "window.ppExport" in html
    assert "window.igvReady" in html


def test_render_html_export_filename_is_sanitised():
    html = render_html(PlotInputs(sample_id='a b/"c`${x}</script>'))
    assert '"sample_file":"a_b_c_x_script_"' in html


HOSTILE_IDS = [
    "A`B",
    "A${alert(1)}B",
    "A\\${globalThis.INJECTED=123}\\`B",
    "A\\B",
    "A</script><script>globalThis.INJECTED=1</script>",
    "A<!--<script>B",
    "A\u2028B\u2029C",
    'A"B\'C',
    "A&amp;B",
]


@pytest.mark.parametrize("sid", HOSTILE_IDS)
def test_hostile_sample_ids(tmp_path, sid):
    """Hostile sample IDs stay data: the page structure is unchanged, the JSON block decodes to the
    exact ID, the header shows it HTML-escaped, the executable scripts are identical to a plain ID's,
    nothing runs, and the ID round-trips into the PURPLE and CNVkit SEG data."""
    import html as _h
    from html.parser import HTMLParser
    cn = load_cnvkit_data(FIXTURES / "sample.cnr", FIXTURES / "sample.call.cns", None)
    _tracks_for(tmp_path, sample_id=sid, purple=_purple(tmp_path), cnvkit=cn)
    out = _tracks_for.last
    assert out["injected"] is None
    assert len(out["segSamples"]) >= 2 and all(s.removesuffix("_cnvkit") == sid for s in out["segSamples"])

    page = (tmp_path / "page.html").read_text(encoding="utf-8")

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.blocks, self._cur = [], None
        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self._cur = [dict(attrs).get("type"), ""]
        def handle_endtag(self, tag):
            if tag == "script" and self._cur is not None:
                self.blocks.append(tuple(self._cur)); self._cur = None
        def handle_data(self, d):
            if self._cur is not None: self._cur[1] += d

    p = Scripts(); p.feed(page)
    assert [t for t, _ in p.blocks] == ["application/json", None, None]   # exactly the 3 expected scripts
    data = json.loads(p.blocks[0][1])
    assert data["seg_data"].split("\n")[1].split("\t")[0] == sid
    assert "<title>IGV bars — " + _h.escape(sid) + "</title>" in page

    plain = render_html(PlotInputs(sample_id="S001"))
    q = Scripts(); q.feed(plain)
    assert [b for t, b in p.blocks if t is None] == [b for t, b in q.blocks if t is None]


POPUP_HARNESS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const dataJson = html.match(/<script type="application\/json" id="pp-data">([\s\S]*?)<\/script>/)[1];
const noop = () => ({ addEventListener() {}, appendChild() {}, remove() {}, click() {}, classList: { toggle() {} }, style: {} });
const ctx = {
  Blob: class { constructor(p) { this.t = p.join(""); } },
  URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} },
  document: { getElementById: (id) => id === "pp-data" ? { textContent: dataJson } : noop(),
              createElement: noop, body: noop() },
  igv: { createBrowser() { return new Promise(() => {}); } },
  location: { search: "" }, URLSearchParams, console, setTimeout,
};
ctx.window = ctx;
require("vm").runInNewContext(scripts[scripts.length - 1], ctx);
const hostile = [{ name: "Sample", value: 'S<b id="x">"' }, "<hr/>", { name: "Value", value: 0.5 }];
process.stdout.write(JSON.stringify({
  seg: ctx.ppPopup({ type: "seg" }, hostile),
  wig: ctx.ppPopup({ type: "wig" }, [{ name: "a<i>", value: 1 }]) === undefined,
  merged: ctx.ppPopup({ type: "merged" }, ["<div style=x>name</div>"]) === undefined,
  annotation: ctx.ppPopup({ type: "annotation" }, [{ name: "Name", value: "<a href='x'>EGFR</a>" }]) === undefined,
  empty: ctx.ppPopup({ type: "seg" }, []),
}));
"""


def test_popup_escapes_segment_tracks_and_leaves_others_to_igv(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    page = tmp_path / "page.html"
    page.write_text(render_html(PlotInputs(sample_id="S001")), encoding="utf-8")
    h = tmp_path / "popup.js"
    h.write_text(POPUP_HARNESS)
    out = json.loads(subprocess.run([node, str(h), str(page)], capture_output=True, text=True,
                                    check=True).stdout)  # nosec B603
    assert "<b" not in out["seg"] and "S&lt;b id=&quot;x&quot;&gt;&quot;" in out["seg"]
    assert out["seg"].count("<hr>") == 1 and "<span>Value</span>" in out["seg"]
    assert out["wig"] is True and out["merged"] is True   # igv.js's own popup (numbers, fixed names, its markup)
    assert out["annotation"] is True          # RefSeq popups keep igv.js's own links/separators
    assert out["empty"] is False


@pytest.mark.parametrize("sid", ["../victim", "/tmp/abs", "a\\b", "..", ".", "", "a\0b"])  # noqa: S108 -- an absolute path the sample_id must reject, never opened
def test_sample_id_must_be_a_plain_file_name(tmp_path, sid):
    with pytest.raises(ValueError):
        write_html(PlotInputs(sample_id=sid), tmp_path / "out")
    assert not (tmp_path / "victim.igv.bars.html").exists()


def test_cli_rejects_path_like_sample_id(tmp_path):
    r = subprocess.run([
        sys.executable, str(SCRIPT), "--sample", "../victim",
        "--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--output_dir", str(tmp_path / "out"),
    ], capture_output=True, text=True)  # nosec B603
    assert r.returncode != 0 and "file name" in r.stderr
    assert not (tmp_path / "victim.igv.bars.html").exists()


def test_cli_accepts_leading_dash_sample_id_in_equals_form(tmp_path):
    """src/code.sh passes --sample=<id>, so an ID starting with '-' is not taken for an option."""
    r = subprocess.run([
        sys.executable, str(SCRIPT), "--sample=-S001",
        "--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--output_dir", str(tmp_path),
    ], capture_output=True, text=True)  # nosec B603
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "-S001.igv.bars.html").exists()
    assert '"--sample=${sample_id}"' in (SCRIPT.parents[4] / "src" / "code.sh").read_text()



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
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--qc_report", ""],
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--qc_report", "/nonexistent/qc.tsv"],
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--msi_report", ""],
    ["--cnvkit_cnr", str(FIXTURES / "sample.cnr"), "--msi_report", "/nonexistent/msi.tsv"],
])
def test_empty_file_argument_is_an_error_not_omitted(tmp_path, args):
    r = subprocess.run([sys.executable, str(SCRIPT), "--sample", "E", *args, "--output_dir", str(tmp_path)],
                       capture_output=True, text=True)  # nosec B603 -- our own CLI with fixed arguments
    assert r.returncode == 1 and r.stderr.startswith("ERROR:") and "does not exist" in r.stderr
    assert "Traceback" not in r.stderr
    assert not (tmp_path / "E.igv.bars.html").exists()
