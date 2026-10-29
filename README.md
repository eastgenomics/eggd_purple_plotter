# eggd_purple_plotter

A DNAnexus app that takes per-sample outputs from the PURPLE/AMBER/CNVkit pipeline and generates a single-file IGV.js HTML copy-number viewer.

igv.js 3.8.3 and all track data are embedded in the HTML, so no server and no third-party script are needed. The hg38 genome (sequence, cytobands, aliases) and the RefSeq annotation are fetched from igv.org and UCSC when the file is opened, so an internet connection to those two hosts is needed to render it. See [Security](#security).

---

## What it does

Given per-sample input files, the app:

1. Validates that at least one of `purple_tar` or `cnvkit_cnr` is present
2. Extracts the PURPLE tar archive (AMBER BAF, target-region CN, PURPLE segments)
3. Reads purity, ploidy, and QC status from the QC report (defaults to `?` if absent)
4. Reads MSI score (defaults to `?` if absent)
5. Converts all available data to BEDGraph/SEG strings
6. Renders a single HTML file with up to 7 igv.js tracks (igv.js and track data embedded)
7. Uploads the HTML and registers it as the `igv_html` output

Missing optional inputs are handled gracefully — tracks are simply omitted.

---

## Inputs

| Name | Class | Required | Description |
|---|---|---|---|
| `sample_id` | string | ✅ | Sample identifier; also the output file name, so it must not contain `/`, `\` or NUL, or be `.`/`..` (the job fails otherwise) |
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
| `igv_html` | file | `{sample_id}.igv.bars.html` — HTML viewer (igv.js and track data embedded; genome fetched when opened) |

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

igv.js 3.8.3 is embedded in the HTML (from `resources/home/dnanexus/purple_plotter/assets/igv.min.js`).

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

### Export an image of the whole-genome view

The viewer header has **Save SVG** and **Save PNG** buttons. They export the
current view (whatever locus is on screen) with a banner carrying the sample ID,
purity, ploidy, status, MSI, genome build and the locus shown, so an image is never separated from its sample or region.

For a file without opening a browser, render the whole-genome view headlessly:

```bash
.venv/bin/pip install playwright==1.64.1          # not needed by the app itself
.venv/bin/python3 resources/home/dnanexus/purple_plotter/render_image.py \
    --html /tmp/26134S0005.igv.bars.html \
    --out_prefix /tmp/26134S0005.genome \
    --chromium_path /usr/bin/chromium            # or set $CHROMIUM_PATH
# -> /tmp/26134S0005.genome.svg, /tmp/26134S0005.genome.png
```

No network is used: igv.js is embedded in the HTML, and the hg38 chromosome-size,
cytoband and alias files are served from
`resources/home/dnanexus/purple_plotter/assets/` (see its README). The image has
no gene-annotation track.

On DNAnexus, set `genome_image=true` to also get `{sample_id}.genome.png` and
`.svg` outputs. Playwright and Chromium come from the `pp_headless_chromium` asset
(source in `asset/`), listed under `runSpec.assetDepends` in `dxapp.json`. The asset
is always downloaded to the worker, even when `genome_image` is false, because
`assetDepends` cannot be conditional.

To rebuild the asset (needs CONTRIBUTE on the target project and a `--bill-to`-able
region; the 001_Reference project is UPLOAD-only for most users, so build in a dev
project and `dx cp` the record):

```bash
dx mkdir -p <project>:/app_assets/pp_headless_chromium/pp_headless_chromium_v1.0.0
dx build_asset asset -d <project>:/app_assets/pp_headless_chromium/pp_headless_chromium_v1.0.0/
```

Current record: `001_Reference:/app_assets/pp_headless_chromium/pp_headless_chromium_v1.0.0/pp_headless_chromium`
(`record-JFV8Y9Q42qG5FFbkGjQq23Kj`). Build takes ~5 min; ~440 MB installed.

### Run on DNAnexus

```bash
# Build (dev/test — creates a fast-iterating applet in a single project)
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

For a release, build and publish the actual **app** object instead — an applet
build is dev-only and is not what end users run:

```bash
dx build --app                       # -> app-xxxx
dx publish app-xxxx                  # makes it available to authorizedUsers
dx run app-eggd_purple_plotter ...    # same -i args as above
```

---

## Security

Every generated viewer has the same structure:

1. A `<meta http-equiv="Content-Security-Policy">` as the first element after `<meta charset>`.
2. One **non-executable** `<script type="application/json" id="pp-data">` block holding every
   per-sample value: all track data, the initial locus and the download file name. It is
   JSON-encoded with `<`, `>`, `&` and all non-ASCII characters escaped, so a sample ID or file
   content can never end the element or start a comment.
3. igv.js 3.8.3, inlined from `assets/igv.min.js` and checked against its SHA-256 when loaded
   (`IGV_JS_SHA256` in `purple_plotter.py`). Its MIT licence is reproduced in an HTML comment
   above it; the DOMPurify notice is inside the script.
4. The viewer code, inlined from `resources/home/dnanexus/purple_plotter/viewer.js`.

Items 3 and 4 are **byte-identical for every sample**. igv.js builds click popups as HTML from
raw values, so for the tracks built from the embedded data (seg, wig, merged) `viewer.js`
supplies its own popup content with every value HTML-escaped (the CSP stops scripts, not
injected markup); the RefSeq annotation keeps igv.js's own popup; the per-sample HTML-context values
(`<title>`, header) are HTML-escaped. Tabs, CR and LF in the sample ID are replaced with `_`
in the SEG track data, where the ID is a tab-delimited column. The CSP lets only those two scripts run, by hash:

```
default-src 'none';
script-src 'sha256-<igv.js>' 'sha256-<viewer.js>';
connect-src blob: https://igv.org https://hgdownload.soe.ucsc.edu;
img-src data: blob:;
style-src 'unsafe-inline';
base-uri 'none';
form-action 'none';
object-src 'none'
```

- `connect-src blob:` — igv.js reads the embedded track data through `blob:` URLs.
- `https://igv.org` — `genomes3.json`, cytobands, aliases, the MANE gene-search index.
- `https://hgdownload.soe.ucsc.edu` — the hg38 `2bit` sequence and the RefSeq track
  (igv.js 3.8.3 rewrites the igv.org hg38 FASTA to UCSC).
- `img-src data: blob:` — igv.js icons, and the PNG export (an SVG `blob:` drawn into an image).
- `style-src 'unsafe-inline'` — igv.js injects `<style>` elements and the legend uses `style=""`.
  Styles cannot run code.
- No `'unsafe-inline'` or `'unsafe-eval'` for scripts, and no `'wasm-unsafe-eval'`: igv.js 3.8.3
  contains a WebAssembly module, but this viewer never instantiates it (checked in a browser).

These directives were established in a real browser by recording `securitypolicyviolation`
events while loading the whole genome, a chromosome, a 2 kb and a 100 bp window (sequence
from UCSC), a gene search (`EGFR`), the RefSeq track, and both Save buttons, from `file://` and
`http://localhost`: no violations. `tests/test_csp.py` repeats this offline in CI, and online
with `PP_LIVE_NETWORK=1`.

If igv.org's genome list is unreachable, igv.js would fall back to `raw.githubusercontent.com`,
and for the sequence to UCSC's AWS mirror. The CSP blocks both, so the viewer then fails to
load rather than reaching other hosts.

A `<meta>` CSP cannot carry `frame-ancestors`, reporting or a report-only mode; a site that
serves the viewers should add the same policy as a response header (with
`frame-ancestors 'none'`).

### Script hashes

```bash
.venv/bin/python3 resources/home/dnanexus/purple_plotter/purple_plotter.py --print-script-hashes
```

prints the two `sha256-…` values. For v1.2.0:

| Script | Hash |
|---|---|
| igv.js 3.8.3 | `sha256-rPTumc3JG+iEIDkw25w5W8hiZ1UpM7SD2gtgnBbqBao=` |
| viewer.js | `sha256-GjFxjH7HR881OR5AW6GaTKGxXSazG6uY5FyT1D/KmKw=` |

Any change to `viewer.js` or `igv.min.js` changes these; record the new values with each release.

---

## Architecture

```
src/code.sh                          ← DNAnexus entry point; all platform I/O here
resources/home/dnanexus/
  purple_plotter/purple_plotter.py   ← Pure Python CLI; no dxpy dependency
  purple_plotter/viewer.js           ← Viewer code, inlined unchanged into every HTML
  purple_plotter/assets/             ← igv.min.js (inlined), its licence, offline genome files
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

# Image-rendering tests run only if playwright and a Chromium are available
# (CHROMIUM_PATH=/usr/bin/chromium); otherwise they are skipped.

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
| igv.js | 3.8.3 (embedded) |
| Genome build | hg38 |

## Sharing outputs by URL

Finished sample HTML outputs can optionally be published to a private S3 bucket fronted
by CloudFront so they can be shared with others by URL, outside of DNAnexus. See
[`DEPLOYMENT.md`](DEPLOYMENT.md) for the live URL, resource names, and how to publish a
new sample.
