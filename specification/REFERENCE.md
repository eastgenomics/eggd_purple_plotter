# REFERENCE — eggd_purple_plotter

## 1. DNAnexus app inputs

| Name | Class | Optional | Default | Description |
|---|---|---|---|---|
| `sample_id` | `string` | No | — | Sample identifier; used in HTML title, header, and output filename |
| `purple_tar` | `file` | Yes | — | `{sample}.purple.tar.gz` from the PURPLE pipeline stage |
| `qc_report` | `file` | Yes | — | `{sample}.qc_report.tsv` from the cgp-qc-flags stage |
| `cnvkit_cnr` | `file` | Yes | — | `{sample}.cnr` from the CNVkit pipeline |
| `cnvkit_call_cns` | `file` | Yes | — | `{sample}.call.cns` from CNVkit |
| `cnvkit_genemetrics` | `file` | Yes | — | `{sample}.genemetrics.csv` from CNVkit |
| `msi_report` | `file` | Yes | — | `{sample}.msi.tsv` from the MSI stage |
| `locus` | `string` | Yes | `"all"` | Initial IGV locus; any valid IGV locus string (e.g. `"chr7:116,672,196-117,120,285"`) |

**Minimum viable inputs:** `sample_id` + (`purple_tar` and/or `cnvkit_cnr`).
App exits 1 if neither `purple_tar` nor `cnvkit_cnr` is present.

## 2. DNAnexus app output

| Name | Class | Description |
|---|---|---|
| `igv_html` | `file` | `{sample_id}.igv.bars.html` — fully self-contained HTML; no server required to view |

---

## 3. Input file formats

### 3.1 PURPLE tar archive (`purple_tar`)

The tar.gz produced by the `cgp-purple` applet. Expected contents (paths relative to archive root):

```
{sample}.amber.baf.tsv.gz         ← gzipped; decompressed before parsing
{sample}.purple.target_region_cn.tsv
{sample}.purple.segment.tsv
```

The loader tries the `.gz` suffix for the BAF file first, then falls back to plain `.tsv`
(for local testing with uncompressed fixtures).

**`amber.baf.tsv` columns used:**

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | e.g. `chr1` |
| `position` | int | 1-based position |
| `tumorBAF` | float | B-allele frequency in tumour (0–1) |

**`target_region_cn.tsv` columns used:**

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | |
| `windowStart` | int | Window start (0-based) |
| `windowEnd` | int | Window end |
| `windowTumorRatio` | float | Tumour/normal depth ratio; rows ≤ 0.05 are skipped |
| `masked` | int | 1 = masked window (skipped) |

**`purple.segment.tsv` columns used:**

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | |
| `start` | int | Segment start |
| `end` | int | Segment end |
| `tumorCopyNumber` | float | Estimated copy number; rows ≤ 0 are skipped |
| `tumorBAF` | float | Fitted BAF for segment |

---

### 3.2 QC report (`qc_report`)

Tab-separated, one header row + one data row.

```
purity	ploidy	status
0.79	2.02	NORMAL
```

| Column | Type | Description |
|---|---|---|
| `purity` | float | Tumour purity (0–1) |
| `ploidy` | float | Average ploidy |
| `status` | string | `NORMAL`, `FAIL_CONTAMINATION`, etc. |

---

### 3.3 MSI report (`msi_report`)

Tab-separated, one header row + one data row.

```
msi_score
3.42
```

| Column | Type | Description |
|---|---|---|
| `msi_score` | float | % unstable microsatellite sites |

Threshold: `msi_score >= 8.0` → `MSI-H`; otherwise `MSS`.

---

### 3.4 CNVkit CNR (`cnvkit_cnr`)

Tab-separated. Standard CNVkit `.cnr` format.

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | |
| `start` | int | Target start (0-based) |
| `end` | int | Target end |
| `log2` | float | Log₂ copy ratio; non-finite values are skipped |

---

### 3.5 CNVkit called segments (`cnvkit_call_cns`)

Tab-separated. Standard CNVkit `.call.cns` format.

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | |
| `start` | int | |
| `end` | int | |
| `log2` | float | Log₂ copy ratio |
| `cn` | int | Integer copy number call (−1 if absent → falls back to `log2`) |
| `probes` | int | Number of probes in segment |

---

### 3.6 CNVkit genemetrics (`cnvkit_genemetrics`)

Comma-separated (`.csv`). Standard CNVkit genemetrics output.

| Column | Type | Description |
|---|---|---|
| `chromosome` | string | |
| `start` | int | |
| `end` | int | |
| `log2` | float | Per-gene log₂ ratio; non-finite values are skipped |

---

## 4. Output file format

A self-contained HTML file. Key structural elements:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <title>IGV bars — {sample_id}</title>
  <!-- inline CSS -->
</head>
<body>
  <div id="hdr">   <!-- sample ID, purity, ploidy, status, MSI -->
  <div id="igv-div">  <!-- igv.js mounts here (shadow DOM) -->
  <div id="leg">   <!-- colour legend -->

  <script src="https://igv.org/web/release/3.8.3/dist/igv.min.js"></script>
  <script>
    // All track data as JS template literals assigned to const variables
    // Each is passed to URL.createObjectURL(new Blob([...]))
    // igv.createBrowser(...) called with 7 tracks
  </script>
</body>
</html>
```

---

## 5. igv.js track configuration

All tracks use **igv.js 3.8.3**. The following track types and config keys are used.

### Track 1 — Coverage + CN bars (`merged`)

A `merged` track combining:
- Four `wig`/bedgraph `bar` sub-tracks (one per CN state: amp, gain, loss, hodel)
- One `wig`/bedgraph `points` sub-track (coverage scatter)

```js
{
  type: "merged", name: "Coverage + CN (log₂)", height: 200,
  min: -2, max: 2, autoscale: false, visibilityWindow: -1, alpha: 0.6,
  tracks: [
    { type: "wig", format: "bedgraph", url: blob(barAmpBg),
      graphType: "bar", color: "#08306b", altColor: "#08306b",
      min: -2, max: 2, autoscale: false, visibilityWindow: -1,
      baselineColor: "#000000" },
    // ... gain, loss, hodel bars ...
    { type: "wig", format: "bedgraph", url: blob(covLog2Bg),
      graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1 },
  ]
}
```

### Tracks 2 & 4 — PURPLE SEG (`seg`)

```js
{
  type: "seg", format: "seg", url: blob(segData), height: 10,
  posColorScale: { low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' },
  negColorScale: { low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' }
}
```

**Critical:** Use `lowColor`/`highColor` CSS strings (3.8.3 API). The old
`lowR`/`lowG`/`lowB` integer fields from 3.0.2 are silently ignored.

### Track 3 — BAF (`merged`)

10 sub-tracks: 5 CN-state-coloured bar pairs (top + bottom arms) + 2 scatter point sub-tracks.

```js
{ type: "merged", name: "BAF", height: 100,
  min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, alpha: 0.6,
  tracks: [ /* 8 bar sub-tracks + 2 scatter sub-tracks */ ] }
```

### Track 5 — CNVkit log₂ scatter (`merged`)

```js
{ type: "merged", name: "CNVkit log₂", height: 150, min: -2, max: 2,
  autoscale: false, visibilityWindow: -1,
  tracks: [
    { type: "wig", format: "bedgraph", url: blob(cnvkitCnrBg),
      graphType: "points", color: "#756bb1", pointSize: 10,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1 }
  ] }
```

### Track 6 — CNVkit segments (`seg`)

Same config as PURPLE SEG tracks above; data from `cns_to_seg()`.

### Track 7 — CNVkit genemetrics (`merged`)

```js
{ type: "merged", name: "CNVkit genemetrics", height: 80, min: -2, max: 2,
  autoscale: false, visibilityWindow: -1,
  tracks: [
    { type: "wig", format: "bedgraph", url: blob(cnvkitGenemetricsBg),
      graphType: "bar", color: "#08306b", altColor: "#8b0000",
      min: -2, max: 2, autoscale: false, visibilityWindow: -1 }
  ] }
```

---

## 6. SEG format

SEG files use tab separation with a header row:

```
SampleID	Chromosome	Start	End	Num_Probes	Seg_Mean
SAMPLE	chr1	1	200000	.	0.0704
SAMPLE	chr2	1	200000	.	0.8074
```

- `Seg_Mean` = log₂(tumorCopyNumber / ploidy) for PURPLE; log₂(max(cn,0.01) / ploidy) for CNVkit (falls back to raw `log2` if `cn` column is absent or −1).
- `Num_Probes` is `.` for PURPLE segments; the `probes` column value for CNVkit segments.

---

## 7. Bedgraph format

All bedgraph data uses UCSC BEDGraph format (0-based half-open coordinates):

```
chr1	0	99999	0.0280
chr1	99999	199999	-0.0280
```

BAF bedgraph coordinates are single-base: `position-1	position` (converting from 1-based AMBER output).

---

## 8. Environment variables

No environment variables are required. DNAnexus provides:

| Variable | Description |
|---|---|
| `$DX_PROJECT_CONTEXT_ID` | Project the job is running in |
| `$DX_JOB_ID` | Current job ID |

Input names from `inputSpec` are available as Bash variables (e.g. `${sample_id}`, `${purple_tar}`).

---

## 9. External dependencies

| Dependency | Purpose | Version |
|---|---|---|
| `pandas` | DataFrame I/O and manipulation | ≥2.0 |
| `igv.js` | Browser-side genome viewer | 3.8.3 (CDN) |
| `dx` CLI | DNAnexus file download/upload in `code.sh` | any (provided by platform) |

All Python stdlib modules used: `argparse`, `gzip`, `io`, `math`, `tarfile`, `pathlib`.

---

## 10. DNAnexus project context

| Item | Value |
|---|---|
| Org | `org-emee_1` |
| Region | `aws:eu-central-1` |
| Target project | `project-J8F1Yq84gPFqgBp4XZ395fB3` |
| Applet destination | `project-J8F1Yq84gPFqgBp4XZ395fB3:/applets/` |
| Instance type | `mem1_ssd1_v2_x2` (7.5 GB RAM, 2 vCPU) |
| Ubuntu release | 24.04 |

---

## 11. Useful links

- igv.js 3.8.3 release: https://github.com/igvteam/igv.js/releases/tag/v3.8.3
- igv.js browser API: https://github.com/igvteam/igv.js/wiki/Browser-Creation
- igv.js track configuration: https://github.com/igvteam/igv.js/wiki/Tracks-2.0
- DNAnexus app development: https://documentation.dnanexus.com/developer/apps
- PURPLE documentation: https://github.com/hartwigmedical/hmftools/tree/master/purple
- CNVkit documentation: https://cnvkit.readthedocs.io/

---

## 12. Glossary

| Term | Definition |
|---|---|
| BAF | B-allele frequency — proportion of reads carrying the B allele at a heterozygous SNP site |
| AMBER | HMF tool producing BAF estimates from a tumour/normal pair |
| COBALT | HMF tool producing depth ratios for copy-number normalisation |
| PURPLE | HMF tool combining AMBER/COBALT output to call copy-number segments and estimate purity/ploidy |
| CN state | Copy-number category (homdel, loss, diploid, gain, amp) defined by log₂(CN/ploidy) thresholds |
| SEG | Segmented copy-number file format used by IGV |
| bedgraph | UCSC format: chrom, start, end, value — used here for scatter/bar track data |
| blob URL | `blob:` URI created by `URL.createObjectURL()` allowing in-memory data to be read as a URL |
| ploidy | Average copy number across the genome, estimated by PURPLE |
| purity | Fraction of the sample that is tumour cells, estimated by PURPLE |
| MSI-H | Microsatellite instability — high; defined as `msi_score ≥ 8.0%` |
| MSS | Microsatellite stable |
| igv.js shadow DOM | igv.js 3.8.3 mounts its UI inside a shadow DOM; standard `document.querySelectorAll` does not pierce it |
