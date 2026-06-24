# eggd_purple_plotter

A DNAnexus app that takes per-sample outputs from the PURPLE/AMBER/CNVkit pipeline and generates a fully self-contained IGV.js HTML copy-number viewer.

The HTML embeds all track data as in-memory blob URLs — no server, no network, no IGV installation required. Open it in a browser.

---

## What it does

Given per-sample input files, the app:

1. Validates that at least one of `purple_tar` or `cnvkit_cnr` is present
2. Extracts the PURPLE tar archive (AMBER BAF, target-region CN, PURPLE segments)
3. Reads purity, ploidy, and QC status from the QC report (defaults to `?` if absent)
4. Reads MSI score (defaults to `?` if absent)
5. Converts all available data to BEDGraph/SEG strings
6. Renders a single self-contained HTML file with 7 igv.js tracks
7. Uploads the HTML and registers it as the `igv_html` output

Missing optional inputs are handled gracefully — tracks are simply omitted.

---

## Inputs

| Name | Class | Required | Description |
|---|---|---|---|
| `sample_id` | string | ✅ | Sample identifier |
| `purple_tar` | file | one of these two | `{sample}.purple.tar.gz` from the PURPLE pipeline stage |
| `cnvkit_cnr` | file | one of these two | `{sample}.cnr` from CNVkit |
| `qc_report` | file | — | `{sample}.qc_report.tsv` (purity, ploidy, status) |
| `cnvkit_call_cns` | file | — | `{sample}.call.cns` from CNVkit |
| `cnvkit_genemetrics` | file | — | `{sample}.genemetrics.csv` from CNVkit |
| `msi_report` | file | — | `{sample}.msi.tsv` |
| `locus` | string | — | Initial IGV locus (default: `all`) |

At least one of `purple_tar` or `cnvkit_cnr` must be supplied. The app exits with an error if both are absent.

## Output

| Name | Class | Description |
|---|---|---|
| `igv_html` | file | `{sample_id}.igv.bars.html` — self-contained HTML viewer |

---

## Tracks in the output HTML

| # | Track | Source |
|---|---|---|
| 1 | Coverage + CN log₂ bars (merged) | PURPLE target-region CN |
| 2 | PURPLE CNV segments (SEG) | PURPLE segments |
| 3 | BAF scatter + fitted BAF bars (merged) | AMBER BAF + PURPLE segments |
| 4 | PURPLE CNV segments strip (SEG) | PURPLE segments |
| 5 | CNVkit log₂ scatter (merged) | CNVkit CNR |
| 6 | CNVkit called segments (SEG) | CNVkit CNS |
| 7 | CNVkit genemetrics bar chart | CNVkit genemetrics |

igv.js 3.8.3 is loaded from CDN at view time (`igv.org/web/release/3.8.3`).

---

## Quick start

### Run locally

```bash
# 1. Clone and create venv
git clone https://github.com/eastgenomics/eggd_purple_plotter
cd eggd_purple_plotter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Run tests
.venv/bin/pytest tests/ -v

# 3. Generate HTML from local files (PURPLE only)
.venv/bin/python3 resources/home/dnanexus/purple_plotter/purple_plotter.py \
    --sample 26134S0005 \
    --purple_tar /path/to/26134S0005.purple.tar.gz \
    --qc_report  /path/to/26134S0005.qc_report.tsv \
    --output_dir /tmp/

# 4. Open the result
open /tmp/26134S0005.igv.bars.html
```

### Run on DNAnexus

```bash
# Build
dx build eggd_purple_plotter/ \
    --destination "project-xxxx:/applets/" --overwrite

# Run
dx run applet-xxxx \
    -isample_id="26134S0005" \
    -ipurple_tar="project-xxxx:file-aaa" \
    -iqc_report="project-xxxx:file-bbb" \
    --destination "project-xxxx:/igv_plots/" \
    --priority high --watch
```

---

## Architecture

```
src/code.sh                          ← DNAnexus entry point; all platform I/O here
resources/home/dnanexus/
  purple_plotter/purple_plotter.py   ← Pure Python CLI; no dxpy dependency
  packages/                          ← Bundled wheels for offline pip install
resources/usr/bin/
  mark-section                       ← DNAnexus structured logging helper
  mark-success                       ← DNAnexus job success marker
tests/                               ← pytest unit + CLI integration tests
```

**`code.sh`** downloads inputs, creates a venv, installs bundled wheels, calls `purple_plotter.py`, uploads the output. It contains no data-conversion logic.

**`purple_plotter.py`** has no DNAnexus dependency — it reads local file paths, converts data to BEDGraph/SEG strings, and renders the HTML. It is fully testable without a DNAnexus connection.

### Bundling pandas for offline install

The DNAnexus worker has no internet access during a job. Bundle the pandas wheel before building the applet:

```bash
pip download pandas \
    --platform manylinux2014_x86_64 \
    --python-version 312 \
    --only-binary=:all: \
    -d resources/home/dnanexus/packages/
```

---

## Development

```bash
# Run all tests
.venv/bin/pytest tests/ -v

# Check no dxpy in the Python module
grep "import dxpy\|from dxpy" resources/home/dnanexus/purple_plotter/purple_plotter.py
# expected: no output

# Validate dxapp.json
python3 -c "import json; json.load(open('dxapp.json')); print('valid')"
```

### CI

GitHub Actions runs `pytest tests/ -v` on every push and pull request (`.github/workflows/pytest.yml`).

---

## Code walkthrough

A 4-part annotated HTML walkthrough of `purple_plotter.py` is in [`docs/`](docs/):

| Part | File | Contents |
|---|---|---|
| 1 | `260623_eggd_purple_plotter_1.html` | High-level outline — phases, data flow, function inventory |
| 2 | `260623_eggd_purple_plotter_2.html` | Key concepts — dataclasses, pandas, tarfile, log₂, BEDGraph/SEG, blob URLs |
| 3 | `260623_eggd_purple_plotter_3.html` | Function walkthrough — all 16 functions |
| 4 | `260623_eggd_purple_plotter_4.html` | Line-by-line reference — 5 hardest functions annotated |

---

## Platform

| Item | Value |
|---|---|
| Org | `org-emee_1` |
| Region | `aws:eu-central-1` |
| Ubuntu | 24.04 |
| Instance | `mem1_ssd1_v2_x2` |
| igv.js | 3.8.3 (CDN) |
| Genome build | hg38 |
