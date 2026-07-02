# IMPLEMENTATION — eggd_purple_plotter

## 0. Prerequisites

- Python 3.12+
- `dx` CLI authenticated to `org-emee_1` (for platform build/test steps only)
- `git` and `gh` CLI

```bash
# Install dev dependencies
python3 -m venv .venv
.venv/bin/pip install pandas pytest
```

---

## 1. Project scaffold

**Target file tree:**

```
eggd_purple_plotter/
├── dxapp.json
├── src/code.sh
├── resources/home/dnanexus/
│   ├── purple_plotter/purple_plotter.py
│   └── packages/                          # populated in M6
├── tests/
│   ├── test_data_loading.py
│   ├── test_conversion.py
│   ├── test_render.py
│   └── test_data/                         # fixtures created in M1
├── requirements.txt
└── .github/workflows/pytest.yml
```

**`requirements.txt`:**
```
pandas
pytest
```

**`.github/workflows/pytest.yml`:**
```yaml
name: pytest
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install pandas pytest
      - run: pytest tests/ -v
```

---

## 2. Milestone plan

| M | Component | Red tests | Green when |
|---|---|---|---|
| M1 | Scaffold + fixtures + dxapp.json | — | `python3 -c "import pandas"` succeeds; all fixture files present |
| M2 | Data-loading functions (`load_*`) | `test_data_loading.py` | All data-loading tests pass |
| M3 | Conversion functions (bedgraph/SEG) | `test_conversion.py` | All conversion tests pass |
| M4 | `render_html` + `HTML_BARS` template | `test_render.py` | Render tests pass; output is valid HTML |
| M5 | CLI `main()` + graceful degradation | `test_render.py::test_render_*` | End-to-end tests pass for all input combinations |
| M6 | `src/code.sh` + bundled packages | manual bash test | Bash validation logic exits 1 when expected |
| M7 | DNAnexus platform test | — | Job completes; `igv_html` output downloadable and renders in browser |

---

## 3. Milestone 1 — Scaffold and fixtures

Create the directory structure and all fixture TSV files with minimal but valid data.

**Fixture: `tests/test_data/sample.amber.baf.tsv`**
```
chromosome	position	tumorBAF	normalBAF
chr1	100000	0.52	0.50
chr1	200000	0.48	0.50
chr2	150000	0.61	0.50
```

**Fixture: `tests/test_data/sample.target_region_cn.tsv`**
```
chromosome	windowStart	windowEnd	windowTumorRatio	masked
chr1	1	100000	1.02	0
chr1	100001	200000	0.98	0
chr2	1	100000	1.50	0
chr2	100001	200000	0.03	0
```

**Fixture: `tests/test_data/sample.purple.segment.tsv`**
```
chromosome	start	end	tumorCopyNumber	tumorBAF
chr1	1	200000	2.1	0.52
chr2	1	100000	3.5	0.55
chr2	100001	200000	1.0	0.50
```

**Fixture: `tests/test_data/sample.qc_report.tsv`**
```
purity	ploidy	status
0.79	2.02	NORMAL
```

**Fixture: `tests/test_data/sample.msi.tsv`**
```
msi_score
3.42
```

**Fixture: `tests/test_data/sample.cnr`**
```
chromosome	start	end	log2	gene	depth	weight
chr1	1	500	0.12	GENE1	120	0.9
chr1	501	1000	-0.45	GENE2	60	0.8
```

**Fixture: `tests/test_data/sample.call.cns`**
```
chromosome	start	end	log2	cn	probes	gene	weight
chr1	1	1000	0.08	2	4	GENE1	0.9
chr2	1	200000	0.75	3	12	GENE2	0.85
```

**Fixture: `tests/test_data/sample.genemetrics.csv`**
```
chromosome	start	end	gene	log2	depth	probes	weight	flag
chr1	1	500	GENE1	0.12	120	4	0.9	0
chr1	501	1000	GENE2	-0.45	60	2	0.8	0
```

**Create `purple_plotter.py` skeleton:**
```python
# resources/home/dnanexus/purple_plotter/purple_plotter.py
"""eggd_purple_plotter — Generate a self-contained IGV bars HTML from PURPLE/CNVkit outputs."""
import argparse
import gzip
import io
import math
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

MSI_THRESHOLD = 8.0
CHR_ORDER = [f"chr{i}" for i in range(1, 23)] + ["chrX"]
CN_STATES = [
    ("hodel",   -1e9, -1.0,  "#8b0000"),
    ("loss",    -1.0, -0.3,  "#fca5a5"),
    ("diploid", -0.3,  0.3,  "#4d4d4d"),
    ("gain",     0.3,  1.0,  "#93c4e0"),
    ("amp",      1.0,  1e9,  "#08306b"),
]

@dataclass
class PurpleData:
    baf_df: pd.DataFrame
    cn_df:  pd.DataFrame
    seg_df: pd.DataFrame

@dataclass
class QCData:
    purity: float = 0.5
    ploidy: float = 2.0
    status: str   = "UNKNOWN"

@dataclass
class MSIData:
    score_str:  str = "?"
    status_str: str = "?"

@dataclass
class CNVkitData:
    cnr_df: "pd.DataFrame | None" = None
    cns_df: "pd.DataFrame | None" = None
    gm_df:  "pd.DataFrame | None" = None

@dataclass
class PlotInputs:
    sample_id: str
    locus:     str        = "all"
    purple:    "PurpleData | None" = None
    qc:        QCData     = field(default_factory=QCData)
    msi:       MSIData    = field(default_factory=MSIData)
    cnvkit:    CNVkitData = field(default_factory=CNVkitData)
```

**Verification:** `python3 -c "from purple_plotter import PlotInputs; print('ok')"` — prints `ok`.

---

## 4. Milestone 2 — Data loading (TDD)

### Red: write tests first

```python
# tests/test_data_loading.py
import gzip
import tarfile
import io
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import load_purple_data, load_qc_data, load_msi_data, load_cnvkit_data
from purple_plotter import QCData, MSIData, CNVkitData

FIXTURES = Path(__file__).parent / "test_data"

def make_purple_tar(tmp_path: Path, sample: str = "sample") -> Path:
    """Build a minimal purple tar.gz containing the three fixture TSVs."""
    tar_path = tmp_path / f"{sample}.purple.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for suffix in ["amber.baf.tsv", "target_region_cn.tsv", "purple.segment.tsv"]:
            src = FIXTURES / f"sample.{suffix}"
            # Store using the naming convention purple_plotter expects
            tf.add(src, arcname=f"{sample}.{suffix}")
    return tar_path

def test_load_purple_data(tmp_path):
    tar = make_purple_tar(tmp_path)
    result = load_purple_data(tar, "sample")
    assert len(result.baf_df) == 3
    assert "tumorBAF" in result.baf_df.columns
    assert len(result.cn_df) == 4
    assert len(result.seg_df) == 3

def test_load_qc_data():
    qc = load_qc_data(FIXTURES / "sample.qc_report.tsv")
    assert abs(qc.purity - 0.79) < 1e-6
    assert abs(qc.ploidy - 2.02) < 1e-6
    assert qc.status == "NORMAL"

def test_load_qc_data_missing_file():
    qc = load_qc_data(None)
    assert qc.purity == 0.5
    assert qc.ploidy == 2.0
    assert qc.status == "UNKNOWN"

def test_load_msi_data():
    msi = load_msi_data(FIXTURES / "sample.msi.tsv")
    assert msi.score_str == "3.42%"
    assert msi.status_str == "MSS"

def test_load_msi_data_none():
    msi = load_msi_data(None)
    assert msi.score_str == "?"
    assert msi.status_str == "?"

def test_load_cnvkit_data():
    result = load_cnvkit_data(
        FIXTURES / "sample.cnr",
        FIXTURES / "sample.call.cns",
        FIXTURES / "sample.genemetrics.csv",
    )
    assert result.cnr_df is not None
    assert result.cns_df is not None
    assert result.gm_df  is not None

def test_load_cnvkit_data_all_none():
    result = load_cnvkit_data(None, None, None)
    assert result.cnr_df is None
    assert result.cns_df is None
    assert result.gm_df  is None
```

### Green: implement load_* functions

```python
def load_purple_data(purple_tar: Path, sample_id: str) -> PurpleData:
    with tarfile.open(purple_tar, "r:gz") as tf:
        def read(suffix: str) -> pd.DataFrame:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith(suffix)), None
            )
            if member is None:
                raise RuntimeError(f"Could not find *{suffix} in {purple_tar.name}")
            raw = tf.extractfile(member).read()
            if suffix.endswith(".gz"):
                raw = gzip.decompress(raw)
            return pd.read_csv(io.BytesIO(raw), sep="\t")
        return PurpleData(
            baf_df = read(f"{sample_id}.amber.baf.tsv.gz") if ... else read("amber.baf.tsv"),
            cn_df  = read("target_region_cn.tsv"),
            seg_df = read("purple.segment.tsv"),
        )
    # Note: the actual tar uses .amber.baf.tsv.gz in production
    # and plain .amber.baf.tsv in test fixtures — try gz first, fall back

def load_qc_data(qc_report: "Path | None") -> QCData:
    if qc_report is None or not Path(qc_report).exists():
        return QCData()
    row = pd.read_csv(qc_report, sep="\t").iloc[0]
    return QCData(
        purity = float(row["purity"]),
        ploidy = float(row["ploidy"]),
        status = str(row.get("status", "UNKNOWN")),
    )

def load_msi_data(msi_report: "Path | None") -> MSIData:
    if msi_report is None or not Path(msi_report).exists():
        return MSIData()
    row   = pd.read_csv(msi_report, sep="\t").iloc[0]
    score = float(row["msi_score"])
    return MSIData(
        score_str  = f"{score:.2f}%",
        status_str = "MSI-H" if score >= MSI_THRESHOLD else "MSS",
    )

def load_cnvkit_data(
    cnr: "Path | None",
    call_cns: "Path | None",
    genemetrics: "Path | None",
) -> CNVkitData:
    def _read(p: "Path | None", sep: str = "\t") -> "pd.DataFrame | None":
        if p is None or not Path(p).exists():
            return None
        return pd.read_csv(p, sep=sep)
    return CNVkitData(
        cnr_df = _read(cnr),
        cns_df = _read(call_cns),
        gm_df  = _read(genemetrics),
    )
```

**Implementation note on `load_purple_data`:** The production PURPLE tar contains
`{sample}.amber.baf.tsv.gz` (gzipped). Test fixtures use plain `.tsv`. Try the `.gz`
suffix first, fall back to plain `.tsv` to support both production and local testing.

**Verification:** `pytest tests/test_data_loading.py -v` — all 7 tests green.

---

## 5. Milestone 3 — Conversion functions (TDD)

### Red: write tests first

```python
# tests/test_conversion.py
from pathlib import Path
import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import (
    coverage_log2_bedgraph, cn_bars_by_state, baf_bedgraph, baf_bedgraph_mirror,
    baf_seg_bars_by_state, to_seg, cnr_to_bedgraph, cns_to_seg, genemetrics_to_bedgraph,
)

def seg_df():
    return pd.DataFrame({
        "chromosome": ["chr1", "chr2"],
        "start":      [1,       1],
        "end":        [200000,  200000],
        "tumorCopyNumber": [2.1, 3.5],
        "tumorBAF":   [0.52, 0.55],
    })

def cn_df():
    return pd.DataFrame({
        "chromosome":       ["chr1", "chr1"],
        "windowStart":      [1,      100001],
        "windowEnd":        [100000, 200000],
        "windowTumorRatio": [1.02,   0.03],   # 0.03 should be skipped
        "masked":           [0,      0],
    })

def baf_df():
    return pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "position":   [100000, 200000],
        "tumorBAF":   [0.52,   0.48],
    })

def test_coverage_log2_bedgraph_skips_low_ratio():
    bg = coverage_log2_bedgraph(cn_df())
    lines = [l for l in bg.splitlines() if l.strip()]
    # Only the row with windowTumorRatio=1.02 should appear (0.03 skipped)
    assert len(lines) == 1
    assert "chr1\t1\t100000" in lines[0]

def test_cn_bars_by_state_gain():
    bars = cn_bars_by_state(seg_df(), ploidy=2.0)
    # chr2 CN=3.5, ploidy=2.0, log2=0.807 → gain
    assert any("chr2" in l for l in bars["gain"].splitlines())
    # chr1 CN=2.1, ploidy=2.0, log2=0.07 → diploid
    assert any("chr1" in l for l in bars["diploid"].splitlines())

def test_baf_bedgraph_values():
    bg = baf_bedgraph(baf_df())
    lines = bg.splitlines()
    assert len(lines) == 2
    # value = tumorBAF - 0.5
    assert "0.0200" in lines[0]   # 0.52 - 0.5

def test_baf_bedgraph_mirror_values():
    bg = baf_bedgraph_mirror(baf_df())
    lines = bg.splitlines()
    assert "-0.0200" in lines[0]  # 0.5 - 0.52

def test_to_seg_header():
    seg = to_seg(seg_df(), "SAMPLE1", ploidy=2.0)
    assert seg.startswith("SampleID\tChromosome")
    lines = [l for l in seg.splitlines() if "SAMPLE1" in l]
    assert len(lines) == 2

def test_cnr_to_bedgraph():
    cnr = pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "start": [1, 501], "end": [500, 1000],
        "log2": [0.12, float("inf")],  # inf should be skipped
    })
    bg = cnr_to_bedgraph(cnr)
    lines = [l for l in bg.splitlines() if l.strip()]
    assert len(lines) == 1
    assert "0.1200" in lines[0]

def test_genemetrics_to_bedgraph_skips_nonfinite():
    gm = pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "start": [1, 501], "end": [500, 1000],
        "log2": [0.12, float("nan")],
    })
    bg = genemetrics_to_bedgraph(gm)
    lines = [l for l in bg.splitlines() if l.strip()]
    assert len(lines) == 1
```

### Green: copy conversion functions from `igv_bars.py`

Copy the following functions verbatim from `cnv-backbone-purple-atlas/scripts/igv_bars.py`
into `purple_plotter.py`:

- `coverage_log2_bedgraph`
- `cn_bars_by_state`
- `baf_bedgraph`
- `baf_bedgraph_mirror`
- `baf_seg_bars_by_state`
- `to_seg`
- `cnr_to_bedgraph`
- `cns_to_seg`
- `genemetrics_to_bedgraph`

No changes needed to these functions — they are already pure (DataFrame → string)
with no DNAnexus dependency.

**Verification:** `pytest tests/test_conversion.py -v` — all 8 tests green.

---

## 6. Milestone 4 — HTML template and `render_html` (TDD)

### Red: write tests first

```python
# tests/test_render.py
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import PlotInputs, QCData, MSIData, render_html

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
    assert "?" in html  # purity shown as ?

def test_render_html_qc_values_shown():
    inputs = PlotInputs(
        sample_id="S001",
        qc=QCData(purity=0.79, ploidy=2.02, status="NORMAL"),
    )
    html = render_html(inputs)
    assert "79%" in html
    assert "2.02" in html
    assert "NORMAL" in html

def test_render_writes_file(tmp_path):
    inputs = PlotInputs(sample_id="S001")
    from purple_plotter import write_html
    out = write_html(inputs, tmp_path)
    assert out.exists()
    assert out.name == "S001.igv.bars.html"
```

### Green: implement `render_html` and `write_html`

Copy `HTML_BARS` template verbatim from `igv_bars.py`.

```python
def render_html(inputs: PlotInputs) -> str:
    """Fill HTML_BARS template from PlotInputs. Empty strings produce empty blob URLs."""
    ploidy = inputs.qc.ploidy

    # PURPLE tracks (empty strings if purple is None)
    if inputs.purple is not None:
        cov_log2_bg = coverage_log2_bedgraph(inputs.purple.cn_df)
        bar_bgs     = cn_bars_by_state(inputs.purple.seg_df, ploidy)
        baf_bg      = baf_bedgraph(inputs.purple.baf_df)
        baf_mir_bg  = baf_bedgraph_mirror(inputs.purple.baf_df)
        baf_bar_bgs = baf_seg_bars_by_state(inputs.purple.seg_df, ploidy)
        seg_data    = to_seg(inputs.purple.seg_df, inputs.sample_id, ploidy)
    else:
        cov_log2_bg = ""
        bar_bgs     = {k: "" for k, *_ in CN_STATES}
        baf_bg = baf_mir_bg = seg_data = ""
        baf_bar_bgs = {f"{k}_{arm}": "" for k, *_ in CN_STATES for arm in ("top","bot")}

    # CNVkit tracks (empty strings if dataframe is None)
    cnvkit_cnr_bg = cnr_to_bedgraph(inputs.cnvkit.cnr_df) if inputs.cnvkit.cnr_df is not None else ""
    cnvkit_sns_bg = cns_to_seg(inputs.cnvkit.cns_df, inputs.sample_id, ploidy) if inputs.cnvkit.cns_df is not None else ""
    cnvkit_gm_bg  = genemetrics_to_bedgraph(inputs.cnvkit.gm_df) if inputs.cnvkit.gm_df is not None else ""

    # Header values
    purity_str = f"{inputs.qc.purity:.0%}" if inputs.qc.purity != 0.5 or inputs.qc.status != "UNKNOWN" else "?"
    ploidy_str = f"{inputs.qc.ploidy:.2f}" if inputs.qc.status != "UNKNOWN" else "?"

    return HTML_BARS.format(
        sample               = inputs.sample_id,
        purity               = purity_str,
        ploidy               = ploidy_str,
        status               = inputs.qc.status,
        msi_score            = inputs.msi.score_str,
        msi_status           = inputs.msi.status_str,
        locus                = inputs.locus,
        cov_log2_bg          = cov_log2_bg,
        bar_amp_bg           = bar_bgs["amp"],
        bar_gain_bg          = bar_bgs["gain"],
        bar_loss_bg          = bar_bgs["loss"],
        bar_hodel_bg         = bar_bgs["hodel"],
        baf_bg               = baf_bg,
        baf_mir_bg           = baf_mir_bg,
        baf_bar_amp_top_bg   = baf_bar_bgs["amp_top"],
        baf_bar_amp_bot_bg   = baf_bar_bgs["amp_bot"],
        baf_bar_gain_top_bg  = baf_bar_bgs["gain_top"],
        baf_bar_gain_bot_bg  = baf_bar_bgs["gain_bot"],
        baf_bar_dip_top_bg   = baf_bar_bgs["diploid_top"],
        baf_bar_dip_bot_bg   = baf_bar_bgs["diploid_bot"],
        baf_bar_loss_top_bg  = baf_bar_bgs["loss_top"],
        baf_bar_loss_bot_bg  = baf_bar_bgs["loss_bot"],
        baf_bar_hodel_top_bg = baf_bar_bgs["hodel_top"],
        baf_bar_hodel_bot_bg = baf_bar_bgs["hodel_bot"],
        seg_data             = seg_data,
        cnvkit_cnr_bg        = cnvkit_cnr_bg,
        cnvkit_sns_bg        = cnvkit_sns_bg,
        cnvkit_gm_bg         = cnvkit_gm_bg,
    )

def write_html(inputs: PlotInputs, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{inputs.sample_id}.igv.bars.html"
    out.write_text(render_html(inputs))
    return out
```

**Note on purity/ploidy display:** When `qc_report` is absent, `QCData` defaults
give `purity=0.5, ploidy=2.0, status="UNKNOWN"`. Display `?` for purity and ploidy
when status is `"UNKNOWN"` so the viewer makes it clear data is missing.

**Verification:** `pytest tests/test_render.py -v` — all 6 tests green.

---

## 7. Milestone 5 — CLI `main()` and graceful degradation (TDD)

### Red: add end-to-end tests

```python
# Append to tests/test_render.py

import subprocess

FIXTURES = Path(__file__).parent / "test_data"
SCRIPT   = Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter/purple_plotter.py"

def _make_tar(tmp_path, sample="sample"):
    import tarfile, gzip
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
```

### Green: implement `main()`

```python
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--sample",             required=True)
    ap.add_argument("--purple_tar",         default=None)
    ap.add_argument("--qc_report",          default=None)
    ap.add_argument("--cnvkit_cnr",         default=None)
    ap.add_argument("--cnvkit_call_cns",    default=None)
    ap.add_argument("--cnvkit_genemetrics", default=None)
    ap.add_argument("--msi_report",         default=None)
    ap.add_argument("--locus",              default="all")
    ap.add_argument("--output_dir",         default=".")
    args = ap.parse_args()

    # Minimum input validation
    if args.purple_tar is None and args.cnvkit_cnr is None:
        ap.error(
            "At least one of --purple_tar or --cnvkit_cnr must be supplied. "
            "Provide PURPLE outputs, CNVkit outputs, or both."
        )

    purple = load_purple_data(Path(args.purple_tar), args.sample) \
             if args.purple_tar else None
    qc     = load_qc_data(Path(args.qc_report) if args.qc_report else None)
    msi    = load_msi_data(Path(args.msi_report) if args.msi_report else None)
    cnvkit = load_cnvkit_data(
        Path(args.cnvkit_cnr)         if args.cnvkit_cnr         else None,
        Path(args.cnvkit_call_cns)    if args.cnvkit_call_cns    else None,
        Path(args.cnvkit_genemetrics) if args.cnvkit_genemetrics else None,
    )

    inputs = PlotInputs(
        sample_id = args.sample,
        locus     = args.locus,
        purple    = purple,
        qc        = qc,
        msi       = msi,
        cnvkit    = cnvkit,
    )

    out = write_html(inputs, Path(args.output_dir))
    print(f"Written: {out}  ({out.stat().st_size // 1024} KB)")

if __name__ == "__main__":
    main()
```

**Verification:** `pytest tests/ -v` — all tests green (data loading + conversion + render + CLI).

---

## 8. Milestone 6 — `dxapp.json`, `code.sh`, bundled packages

### `dxapp.json`

```json
{
  "name": "eggd_purple_plotter",
  "title": "PURPLE IGV bars plotter",
  "summary": "Generates a self-contained igv.js HTML viewer from PURPLE/AMBER/CNVkit per-sample outputs",
  "dxapi": "1.0.0",
  "version": "1.0.0",
  "properties": { "githubRelease": "1.0.0" },
  "developers":       ["org-emee_1"],
  "authorizedUsers":  ["org-emee_1"],
  "inputSpec": [
    { "name": "sample_id",           "class": "string", "label": "Sample ID" },
    { "name": "purple_tar",          "class": "file",   "label": "PURPLE tar.gz",           "optional": true },
    { "name": "qc_report",           "class": "file",   "label": "QC report TSV",           "optional": true },
    { "name": "cnvkit_cnr",          "class": "file",   "label": "CNVkit CNR",              "optional": true },
    { "name": "cnvkit_call_cns",     "class": "file",   "label": "CNVkit called segments",  "optional": true },
    { "name": "cnvkit_genemetrics",  "class": "file",   "label": "CNVkit genemetrics CSV",  "optional": true },
    { "name": "msi_report",          "class": "file",   "label": "MSI report TSV",          "optional": true },
    { "name": "locus",               "class": "string", "label": "Initial IGV locus",       "optional": true, "default": "all" }
  ],
  "outputSpec": [
    { "name": "igv_html", "class": "file", "label": "IGV bars HTML viewer" }
  ],
  "runSpec": {
    "distribution": "Ubuntu",
    "release":      "24.04",
    "version":      "0",
    "interpreter":  "bash",
    "file":         "src/code.sh",
    "timeoutPolicy": { "*": { "hours": 1 } }
  },
  "regionalOptions": {
    "aws:eu-central-1": {
      "systemRequirements": { "*": { "instanceType": "mem1_ssd1_v2_x2" } }
    }
  }
}
```

### `src/code.sh`

```bash
#!/bin/bash
set -exo pipefail

main() {
    mark-section "Validating inputs"
    if [ -z "${purple_tar:-}" ] && [ -z "${cnvkit_cnr:-}" ]; then
        echo "ERROR: at least one of purple_tar or cnvkit_cnr must be provided" >&2
        exit 1
    fi

    mark-section "Downloading inputs"
    dx-download-all-inputs --parallel

    mark-section "Installing packages"
    python3 -m venv /tmp/ppenv
    /tmp/ppenv/bin/pip install --no-index --no-deps /home/dnanexus/packages/*

    mark-section "Building arguments"
    args="--sample ${sample_id} --output_dir ~/"

    [ -n "${purple_tar:-}" ]         && args+=" --purple_tar $(ls ~/in/purple_tar/*)"
    [ -n "${qc_report:-}" ]          && args+=" --qc_report $(ls ~/in/qc_report/*)"
    [ -n "${locus:-}" ]              && args+=" --locus ${locus}"
    [ -n "${cnvkit_cnr:-}" ]         && args+=" --cnvkit_cnr $(ls ~/in/cnvkit_cnr/*)"
    [ -n "${cnvkit_call_cns:-}" ]    && args+=" --cnvkit_call_cns $(ls ~/in/cnvkit_call_cns/*)"
    [ -n "${cnvkit_genemetrics:-}" ] && args+=" --cnvkit_genemetrics $(ls ~/in/cnvkit_genemetrics/*)"
    [ -n "${msi_report:-}" ]         && args+=" --msi_report $(ls ~/in/msi_report/*)"

    mark-section "Generating IGV bars HTML"
    /tmp/ppenv/bin/python3 /home/dnanexus/purple_plotter/purple_plotter.py $args

    mark-section "Uploading output"
    igv_html=$(dx upload ~/${sample_id}.igv.bars.html --brief)
    dx-jobutil-add-output igv_html "${igv_html}" --class=file

    mark-success
}
```

### Bundle packages

```bash
# Run from repo root — downloads pandas wheel for Ubuntu 24.04 / Python 3.12
pip download pandas \
    --platform manylinux2014_x86_64 \
    --python-version 312 \
    --only-binary=:all: \
    -d resources/home/dnanexus/packages/
```

**Verification:**
```bash
bash -c 'purple_tar=""; cnvkit_cnr=""; [ -z "${purple_tar:-}" ] && [ -z "${cnvkit_cnr:-}" ] && echo "EXIT1" || echo "PASS"'
# Expected output: EXIT1
```

---

## 9. Milestone 7 — DNAnexus platform test

```bash
# Build applet
dx mkdir -p "project-J8F1Yq84gPFqgBp4XZ395fB3:/applets/"
NEW_ID=$(dx build eggd_purple_plotter/ \
    --destination "project-J8F1Yq84gPFqgBp4XZ395fB3:/applets/" \
    --overwrite 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Applet: $NEW_ID"

# Test run with real sample (purple_tar + qc_report only)
dx run "$NEW_ID" \
    -isample_id="26134S0005" \
    -ipurple_tar="project-J8F1Yq84gPFqgBp4XZ395fB3:file-<purple_tar_id>" \
    -iqc_report="project-J8F1Yq84gPFqgBp4XZ395fB3:file-<qc_report_id>" \
    --destination "project-J8F1Yq84gPFqgBp4XZ395fB3:/test_igv_plots/" \
    --priority high \
    --watch

# Download and spot-check
dx download project-J8F1Yq84gPFqgBp4XZ395fB3:/test_igv_plots/26134S0005.igv.bars.html
open 26134S0005.igv.bars.html   # verify in browser
```

**Verification:** Job completes (state=`done`); HTML file is downloadable; opens in
browser and shows igv.js tracks without JavaScript errors.

---

## 10. Final checks

```bash
# All unit tests pass
pytest tests/ -v

# No dxpy imports in purple_plotter.py
grep -n "import dxpy\|from dxpy" resources/home/dnanexus/purple_plotter/purple_plotter.py
# Expected: no output

# HTML template contains required CDN URL
grep "igv.org/web/release/3.8.3" resources/home/dnanexus/purple_plotter/purple_plotter.py
# Expected: 1 match

# code.sh uses set -exo pipefail (not -euxo — no -u)
head -3 src/code.sh | grep "set -exo pipefail"
# Expected: match found

# dxapp.json is valid JSON
python3 -c "import json; json.load(open('dxapp.json')); print('valid')"
```
