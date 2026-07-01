# eggd_purple_plotter — IGV bars HTML viewer for PURPLE/CNVkit CNV outputs

`eggd_purple_plotter` is a DNAnexus app that takes per-sample outputs from the
PURPLE/AMBER/CNVkit pipeline and generates a fully self-contained IGV.js HTML
file showing copy-number and allele-frequency tracks. The HTML embeds all track
data as in-memory blob URLs — no server is required to view it. igv.js 3.8.3 is
loaded from the igv.org CDN at view time, so an internet connection is needed to
render the tracks.

## What this app does

Given per-sample input files, the app:

1. Downloads all supplied input files via `dx-download-all-inputs`.
2. Validates that at least one of `purple_tar` or `cnvkit_cnr` is present; exits with a clear error otherwise.
3. Extracts the PURPLE tar archive to obtain AMBER BAF, target-region CN, and PURPLE segment TSVs (if supplied).
4. Reads the QC report for purity, ploidy, and QC status (if supplied; defaults to `?` if absent).
5. Reads MSI score (if supplied; defaults to `?` if absent).
6. Converts all available data to bedgraph/SEG strings; omits tracks for any absent input.
7. Renders a single self-contained HTML file embedding all data as JavaScript blob URLs.
8. Uploads the HTML file and sets it as the `igv_html` output.

All data-conversion and HTML-generation logic lives in a pure Python module
(`purple_plotter.py`) with no dxpy dependency — it is fully testable locally.

## Status of this document set

These documents are the **complete design and build specification**. A fresh
agent session (or a human developer) should be able to open this directory, read
the files in order, and build a working, tested app without needing the original
conversation.

Read in this order:

1. **README.md** (this file) — orientation and quick start
2. **DESIGN.md** — architecture, module responsibilities, data model, error strategy, testing strategy
3. **IMPLEMENTATION.md** — milestone-by-milestone TDD build plan with code sketches
4. **REFERENCE.md** — input/output file formats, igv.js track config, env vars, external dependencies, glossary

## Project layout (target)

```
eggd_purple_plotter/
├── dxapp.json                                  # App metadata, inputs, outputs, run spec
├── src/
│   └── code.sh                                 # Bash entry point — all DNAnexus I/O here
├── resources/
│   └── home/dnanexus/
│       ├── purple_plotter/
│       │   └── purple_plotter.py               # Pure Python CLI — no dxpy, locally testable
│       └── packages/                           # Bundled .whl files (offline pip install)
│           └── pandas-*.whl                    # Only non-stdlib dependency
├── tests/
│   ├── test_data_loading.py                    # Unit tests for load_* functions
│   ├── test_conversion.py                      # Unit tests for bedgraph/SEG converters
│   ├── test_render.py                          # End-to-end HTML render test
│   └── test_data/
│       ├── sample.amber.baf.tsv                # Minimal AMBER BAF fixture (10 rows)
│       ├── sample.target_region_cn.tsv         # Minimal target-region CN fixture
│       ├── sample.purple.segment.tsv           # Minimal PURPLE segment fixture
│       ├── sample.qc_report.tsv                # QC report fixture
│       ├── sample.msi.tsv                      # MSI report fixture
│       ├── sample.cnr                          # CNVkit CNR fixture
│       ├── sample.call.cns                     # CNVkit called segments fixture
│       └── sample.genemetrics.csv              # CNVkit genemetrics fixture
├── requirements.txt                            # Local dev only (pandas, pytest)
├── README.md                                   # End-user readme (build from this spec)
└── .github/
    └── workflows/
        └── pytest.yml                          # CI: runs pytest on push/PR
```

## Quick start (once built)

```bash
# 1. Clone
git clone https://github.com/woook/eggd_purple_plotter
cd eggd_purple_plotter

# 2. Create venv and install dev deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Run tests
.venv/bin/pytest tests/ -v

# 4. Run locally on real files
.venv/bin/python3 resources/home/dnanexus/purple_plotter/purple_plotter.py \
    --sample 26134S0005 \
    --purple_tar /path/to/26134S0005.purple.tar.gz \
    --qc_report  /path/to/26134S0005.qc_report.tsv \
    --output_dir /tmp/

# 5. Build the DNAnexus applet
dx mkdir -p "project-xxxx:/applets/"
dx build eggd_purple_plotter/ --destination "project-xxxx:/applets/" --overwrite

# 6. Run on DNAnexus
dx run applet-xxxx \
    -isample_id="26134S0005" \
    -ipurple_tar="project-xxxx:file-aaa" \
    -iqc_report="project-xxxx:file-bbb" \
    --destination "project-xxxx:/igv_plots/" \
    --priority high --watch
```

## Target user

A clinical bioinformatician at East Genomics running the PURPLE/AMBER/CNVkit
tumour copy-number pipeline on DNAnexus. They receive an `igv_html` output file
for each sample that they can open directly in a browser to review CN profiles,
BAF, and segment calls without installing any software.

## Non-goals

- Does not run PURPLE, AMBER, COBALT, or CNVkit — it consumes their outputs only.
- Does not produce a cohort-level index page linking multiple samples.
- Does not perform any variant filtering, re-calling, or QC flagging.
- Does not upload the HTML to S3 or any external storage — that is a separate step.
- Does not support WGS BAM visualisation (no BAM/BAI inputs).
- Does not embed the igv.js library locally — it loads igv.js 3.8.3 from `igv.org` CDN at view time.
- Does not support genome builds other than hg38.
