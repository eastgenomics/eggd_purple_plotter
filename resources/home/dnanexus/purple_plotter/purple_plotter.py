"""eggd_purple_plotter — Generate a self-contained IGV bars HTML from PURPLE/CNVkit outputs."""
import argparse
import gzip
import html as _html
import io
import math
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# ── Constants ──────────────────────────────────────────────────────────────────

MSI_THRESHOLD = 8.0
CHR_ORDER = [f"chr{i}" for i in range(1, 23)] + ["chrX"]
CN_STATES = [
    ("hodel",   -1e9, -1.0,  "#8b0000"),
    ("loss",    -1.0, -0.3,  "#fca5a5"),
    ("diploid", -0.3,  0.3,  "#4d4d4d"),
    ("gain",     0.3,  1.0,  "#93c4e0"),
    ("amp",      1.0,  1e9,  "#08306b"),
]

# ── Data model ─────────────────────────────────────────────────────────────────

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
    loaded: bool  = False  # True only when a QC file was successfully read

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
    locus:     str               = "all"
    purple:    "PurpleData | None" = None
    qc:        QCData            = field(default_factory=QCData)
    msi:       MSIData           = field(default_factory=MSIData)
    cnvkit:    CNVkitData        = field(default_factory=CNVkitData)


# ── Data loading ───────────────────────────────────────────────────────────────

def load_purple_data(purple_tar: Path) -> PurpleData:
    """Extract amber.baf.tsv, target_region_cn.tsv, purple.segment.tsv from tar.gz."""
    with tarfile.open(purple_tar, "r:gz") as tf:
        members = tf.getmembers()

        def read_by_suffix(suffix: str) -> pd.DataFrame:
            member = next((m for m in members if m.name.endswith(suffix)), None)
            if member is None:
                raise RuntimeError(
                    f"Could not find *{suffix} in {Path(purple_tar).name}"
                )
            return pd.read_csv(io.BytesIO(tf.extractfile(member).read()), sep="\t")

        # BAF: production tar has .amber.baf.tsv.gz (gzipped); test fixtures are plain .tsv
        baf_gz_member = next(
            (m for m in members if m.name.endswith(".amber.baf.tsv.gz")), None
        )
        if baf_gz_member is not None:
            raw = gzip.decompress(tf.extractfile(baf_gz_member).read())
            baf_df = pd.read_csv(io.BytesIO(raw), sep="\t")
        else:
            baf_df = read_by_suffix("amber.baf.tsv")

        return PurpleData(
            baf_df = baf_df,
            cn_df  = read_by_suffix("target_region_cn.tsv"),
            seg_df = read_by_suffix("purple.segment.tsv"),
        )


def load_qc_data(qc_report: "Path | None") -> QCData:
    """Parse purity, ploidy, status from qc_report.tsv. Returns defaults on missing file."""
    if qc_report is None or not Path(qc_report).exists():
        return QCData()
    row = pd.read_csv(qc_report, sep="\t").iloc[0]
    val = row.get("status", pd.NA)
    return QCData(
        purity = float(row["purity"]),
        ploidy = float(row["ploidy"]),
        status = "UNKNOWN" if pd.isna(val) else str(val),
        loaded = True,
    )


def load_msi_data(msi_report: "Path | None") -> MSIData:
    """Parse msi_score from msi.tsv. Returns ('?', '?') if path is None."""
    if msi_report is None or not Path(msi_report).exists():
        return MSIData()
    row   = pd.read_csv(msi_report, sep="\t").iloc[0]
    score = float(row["msi_score"])
    return MSIData(
        score_str  = f"{score:.2f}%",
        status_str = "MSI-H" if score >= MSI_THRESHOLD else "MSS",
    )


def load_cnvkit_data(
    cnr:         "Path | None",
    call_cns:    "Path | None",
    genemetrics: "Path | None",
) -> CNVkitData:
    """Load CNVkit files. Any absent path → None DataFrame in returned dataclass."""
    def _read(p: "Path | None") -> "pd.DataFrame | None":
        if p is None or not Path(p).exists():
            return None
        return pd.read_csv(p, sep="\t")
    return CNVkitData(
        cnr_df = _read(cnr),
        cns_df = _read(call_cns),
        gm_df  = _read(genemetrics),
    )


# ── Data conversion ───────────────────────────────────────────────────────────────

def coverage_log2_bedgraph(cn_df: pd.DataFrame) -> str:
    """log₂(windowTumorRatio) bedgraph — skips windows with ratio ≤ 0.05."""
    lines: list[str] = []
    for chrom in CHR_ORDER:
        sub = cn_df[
            (cn_df["chromosome"] == chrom)
            & (~cn_df["masked"].astype(bool))
            & (cn_df["windowTumorRatio"] > 0.05)
        ]
        for _, row in sub.iterrows():
            log2_r = math.log2(float(row["windowTumorRatio"]))
            lines.append(f"{chrom}\t{int(row['windowStart'])}\t{int(row['windowEnd'])}\t{log2_r:.4f}")
    return "\n".join(lines)


def cn_bars_by_state(seg_df: pd.DataFrame, ploidy: float) -> dict[str, str]:
    """log₂(CN/ploidy) per segment split by CN state — one interval per segment."""
    denom   = max(ploidy, 0.1)
    buckets = {key: [] for key, *_ in CN_STATES}
    for chrom in CHR_ORDER:
        for _, row in seg_df[seg_df["chromosome"] == chrom].iterrows():
            cn = float(row.get("tumorCopyNumber", 0))
            if cn <= 0:
                continue
            log2_r = math.log2(cn / denom)
            start, end = int(row["start"]), int(row["end"])
            for key, lo, hi, _ in CN_STATES:
                if lo <= log2_r < hi:
                    buckets[key].append(f"{chrom}\t{start}\t{end}\t{log2_r:.4f}")
                    break
    return {k: "\n".join(v) for k, v in buckets.items()}


def baf_bedgraph(baf_df: pd.DataFrame) -> str:
    """tumorBAF − 0.5 for each AMBER site (top arm)."""
    lines: list[str] = []
    for chrom in CHR_ORDER:
        for _, row in baf_df[baf_df["chromosome"] == chrom].iterrows():
            val = float(row["tumorBAF"])
            if 0.0 <= val <= 1.0:
                pos = int(row["position"])
                lines.append(f"{chrom}\t{pos-1}\t{pos}\t{val - 0.5:.4f}")
    return "\n".join(lines)


def baf_bedgraph_mirror(baf_df: pd.DataFrame) -> str:
    """0.5 − tumorBAF for each AMBER site (bottom arm)."""
    lines: list[str] = []
    for chrom in CHR_ORDER:
        for _, row in baf_df[baf_df["chromosome"] == chrom].iterrows():
            val = float(row["tumorBAF"])
            if 0.0 <= val <= 1.0:
                pos = int(row["position"])
                lines.append(f"{chrom}\t{pos-1}\t{pos}\t{0.5 - val:.4f}")
    return "\n".join(lines)


def baf_seg_bars_by_state(seg_df: pd.DataFrame, ploidy: float) -> dict[str, str]:
    """Fitted BAF bar chart split by CN state, mirrored top + bottom arms."""
    denom   = max(ploidy, 0.1)
    buckets = {f"{k}_{arm}": [] for k, *_ in CN_STATES for arm in ("top", "bot")}
    for chrom in CHR_ORDER:
        sub = seg_df[
            (seg_df["chromosome"] == chrom)
            & (seg_df["tumorCopyNumber"] > 0)
            & (seg_df["tumorBAF"] > 0)
        ]
        for _, row in sub.iterrows():
            cn    = float(row["tumorCopyNumber"])
            baf   = min(1.0, max(0.0, float(row["tumorBAF"])))
            start, end = int(row["start"]), int(row["end"])
            log2_r    = math.log2(cn / denom)
            state_key = CN_STATES[-1][0]
            for key, lo, hi, _ in CN_STATES:
                if lo <= log2_r < hi:
                    state_key = key
                    break
            buckets[f"{state_key}_top"].append(f"{chrom}\t{start}\t{end}\t{baf - 0.5:.4f}")
            buckets[f"{state_key}_bot"].append(f"{chrom}\t{start}\t{end}\t{0.5 - baf:.4f}")
    return {k: "\n".join(v) for k, v in buckets.items()}


def to_seg(seg_df: pd.DataFrame, sample: str, ploidy: float) -> str:
    """PURPLE segment.tsv → IGV SEG (log₂(CN/ploidy))."""
    denom = max(ploidy, 0.1)
    lines = ["SampleID\tChromosome\tStart\tEnd\tNum_Probes\tSeg_Mean"]
    for chrom in CHR_ORDER:
        for _, row in seg_df[seg_df["chromosome"] == chrom].iterrows():
            cn = float(row.get("tumorCopyNumber", 0))
            if cn <= 0:
                continue
            lines.append(
                f"{sample}\t{chrom}\t{int(row['start'])}\t{int(row['end'])}"
                f"\t.\t{math.log2(cn / denom):.4f}"
            )
    return "\n".join(lines)


def cnr_to_bedgraph(cnr_df: pd.DataFrame) -> str:
    """CNVkit .cnr per-target log₂ ratios → bedgraph."""
    lines: list[str] = []
    for chrom in CHR_ORDER:
        sub = cnr_df[cnr_df["chromosome"] == chrom].sort_values("start")
        for _, row in sub.iterrows():
            log2 = float(row["log2"])
            if math.isfinite(log2):
                lines.append(f"{chrom}\t{int(row['start'])}\t{int(row['end'])}\t{log2:.4f}")
    return "\n".join(lines)


def cns_to_seg(cns_df: pd.DataFrame, sample: str, ploidy: float) -> str:
    """CNVkit .call.cns → IGV SEG (log₂(CN/ploidy), or raw log₂ if cn absent)."""
    denom = max(ploidy, 0.1)
    lines = ["SampleID\tChromosome\tStart\tEnd\tNum_Probes\tSeg_Mean"]
    for chrom in CHR_ORDER:
        for _, row in cns_df[cns_df["chromosome"] == chrom].iterrows():
            cn = float(row.get("cn", -1))
            if cn >= 0:
                seg_mean = math.log2(max(cn, 0.01) / denom)
            else:
                raw = float(row.get("log2", float("nan")))
                if not math.isfinite(raw):
                    continue
                seg_mean = raw
            lines.append(
                f"{sample}_cnvkit\t{chrom}\t{int(row['start'])}\t{int(row['end'])}"
                f"\t{int(row.get('probes', 0))}\t{seg_mean:.4f}"
            )
    return "\n".join(lines)


def genemetrics_to_bedgraph(gm_df: pd.DataFrame) -> str:
    """CNVkit .genemetrics.csv → bedgraph of log₂ values."""
    lines: list[str] = []
    for chrom in CHR_ORDER:
        sub = gm_df[gm_df["chromosome"] == chrom].sort_values("start")
        for _, row in sub.iterrows():
            log2_val = float(row["log2"])
            if math.isfinite(log2_val):
                lines.append(f"{chrom}\t{int(row['start'])}\t{int(row['end'])}\t{log2_val:.4f}")
    return "\n".join(lines)


# ── HTML template ──────────────────────────────────────────────────────────────

HTML_BARS = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>IGV bars — {sample}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; }}
#hdr {{ background: #1a2638; color: #e8edf3; padding: 10px 18px;
        display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
#hdr .ttl  {{ font-size: 15px; font-weight: 600; }}
#hdr .meta {{ font-size: 12px; color: #9aafc4; }}
#hdr .btns {{ margin-left: auto; display: flex; gap: 8px; }}
#hdr button {{ font: inherit; font-size: 12px; color: #e8edf3; background: #2c3e57;
               border: 1px solid #4a617f; border-radius: 3px; padding: 3px 10px; cursor: pointer; }}
#hdr button:hover {{ background: #3a516f; }}
#igv-div {{ background: white; }}
#leg {{ background: #fff; border-top: 1px solid #dde; padding: 7px 18px;
        display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
        font-size: 11.5px; color: #444; }}
.li {{ display:flex; align-items:center; gap:5px; }}
.sw {{ width:12px; height:12px; border-radius:2px; flex-shrink:0; }}
.note {{ color:#888; font-style:italic; }}
</style>
</head>
<body>

<div id="hdr">
  <span class="ttl">CNV viewer (bars) — {sample}</span>
  <span class="meta">
    Purity {purity} &nbsp;·&nbsp; Ploidy {ploidy} &nbsp;·&nbsp; {status}
    &nbsp;·&nbsp; MSI {msi_score} &nbsp;·&nbsp; {msi_status}
  </span>
  <span class="btns">
    <button type="button" id="btn-svg" title="Download the current view as SVG">Save SVG</button>
    <button type="button" id="btn-png" title="Download the current view as PNG">Save PNG</button>
  </span>
</div>
<div id="igv-div"></div>
<div id="leg">
  <strong>Tracks:</strong>
  <div class="li"><div class="sw" style="background:rgba(80,80,80,0.5)"></div>Coverage ratio (PURPLE)</div>
  <div class="li"><div class="sw" style="background:#08306b"></div>CN amp (&gt;1 log&#x2082;)</div>
  <div class="li"><div class="sw" style="background:#93c4e0"></div>CN gain (0.3&#x2013;1.0 log&#x2082;)</div>
  <div class="li"><div class="sw" style="background:#fca5a5"></div>CN loss (&#x2212;1.0 to &#x2212;0.3 log&#x2082;)</div>
  <div class="li"><div class="sw" style="background:#8b0000"></div>CN homdel (&lt;&#x2212;1.0 log&#x2082;)</div>
  <div class="li"><div class="sw" style="background:rgba(80,80,80,0.5)"></div>BAF (AMBER scatter)</div>
  <div class="li"><div class="sw" style="background:#4d4d4d"></div>Fitted BAF (coloured by CN state)</div>
  <div class="li"><div class="sw" style="background:#08306b"></div>Seg gain</div>
  <div class="li"><div class="sw" style="background:#8b0000"></div>Seg loss</div>
  <div class="li"><div class="sw" style="background:#756bb1"></div>CNVkit log&#x2082; ratio (per target)</div>
  <div class="li"><div class="sw" style="background:#08306b"></div>CNVkit seg gain</div>
  <div class="li"><div class="sw" style="background:#8b0000"></div>CNVkit seg loss</div>
  <div class="li"><div class="sw" style="background:#08306b"></div><div class="sw" style="background:#8b0000"></div>CNVkit genemetrics (gain / loss)</div>
  <span class="note">
    Coverage y: &#x2212;2&#x2013;+2 &nbsp;&#xb7;&nbsp;
    Bars y: log&#x2082;(CN/ploidy); 0&#xa0;=&#xa0;diploid &nbsp;&#xb7;&nbsp;
    CNVkit y: log&#x2082; ratio; 0&#xa0;=&#xa0;diploid &nbsp;&#xb7;&nbsp;
    BAF y: 0&#x2013;1
  </span>
</div>

<script src="https://igv.org/web/release/3.8.3/dist/igv.min.js"></script>
<script>
const covLog2Bg          = `{cov_log2_bg}`;
const barAmpBg           = `{bar_amp_bg}`;
const barGainBg          = `{bar_gain_bg}`;
const barLossBg          = `{bar_loss_bg}`;
const barHodelBg         = `{bar_hodel_bg}`;
const bafBg              = `{baf_bg}`;
const bafMirBg           = `{baf_mir_bg}`;
const bafBarAmpTopBg     = `{baf_bar_amp_top_bg}`;
const bafBarAmpBotBg     = `{baf_bar_amp_bot_bg}`;
const bafBarGainTopBg    = `{baf_bar_gain_top_bg}`;
const bafBarGainBotBg    = `{baf_bar_gain_bot_bg}`;
const bafBarLossTopBg    = `{baf_bar_loss_top_bg}`;
const bafBarLossBotBg    = `{baf_bar_loss_bot_bg}`;
const bafBarHodelTopBg   = `{baf_bar_hodel_top_bg}`;
const bafBarHodelBotBg   = `{baf_bar_hodel_bot_bg}`;
const cnvkitCnrBg        = `{cnvkit_cnr_bg}`;
const cnvkitSnsBg        = `{cnvkit_sns_bg}`;
const cnvkitGenemetricsBg = `{cnvkit_gm_bg}`;
const segData            = `{seg_data}`;

// Blob URLs whose data is empty (input not supplied). igv.js throws while
// parsing an empty SEG file and aborts createBrowser, leaving a blank viewer,
// so tracks backed by empty data are removed before the browser is created.
const emptyBlobs = new Set();
function blob(txt) {{
  const url = URL.createObjectURL(new Blob([txt], {{ type: "text/plain" }}));
  if (!txt.trim()) emptyBlobs.add(url);
  return url;
}}

function pruneTracks(list) {{
  return list.map(function (t) {{
    if (t.tracks) {{
      const kept = t.tracks.filter(function (c) {{ return !emptyBlobs.has(c.url); }});
      return kept.length ? Object.assign({{}}, t, {{ tracks: kept }}) : null;
    }}
    return emptyBlobs.has(t.url) ? null : t;
  }}).filter(Boolean);
}}

igv.createBrowser(document.getElementById("igv-div"), {{
  genome:    "hg38",
  locus:     new URLSearchParams(location.search).get("locus") || "{locus}",
  showRuler: true,
  showCursorTrackingGuide: true,
  tracks: pruneTracks([

    // ── 1. Coverage scatter + CN log₂ bars ──────────────────────────────────
    {{
      type: "merged", name: "Coverage + CN (log\u2082)", height: 200,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1, alpha: 0.6,
      tracks: [
        {{ type: "wig", format: "bedgraph", url: blob(barAmpBg),
           graphType: "bar", color: "#08306b", altColor: "#08306b",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(barGainBg),
           graphType: "bar", color: "#93c4e0", altColor: "#93c4e0",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(barLossBg),
           graphType: "bar", color: "#fca5a5", altColor: "#fca5a5",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(barHodelBg),
           graphType: "bar", color: "#8b0000", altColor: "#8b0000",
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(covLog2Bg),
           graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6,
           min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
      ],
    }},

    // ── 2. PURPLE CNV segments (log₂ SEG) ───────────────────────────────────
    {{
      name: " ", type: "seg", format: "seg",
      url: blob(segData), height: 10,
      posColorScale: {{ low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' }},
      negColorScale: {{ low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' }},
    }},

    // ── 3. BAF scatter + fitted BAF bars ────────────────────────────────────
    {{
      type: "merged", name: "BAF", height: 100,
      min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, alpha: 0.6,
      tracks: [
        {{ type: "wig", format: "bedgraph", url: blob(bafBarAmpTopBg),   graphType: "bar", color: "#08306b", altColor: "#08306b", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarGainTopBg),  graphType: "bar", color: "#93c4e0", altColor: "#93c4e0", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarLossTopBg),  graphType: "bar", color: "#fca5a5", altColor: "#fca5a5", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarHodelTopBg), graphType: "bar", color: "#8b0000", altColor: "#8b0000", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarAmpBotBg),   graphType: "bar", color: "#08306b", altColor: "#08306b", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarGainBotBg),  graphType: "bar", color: "#93c4e0", altColor: "#93c4e0", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarLossBotBg),  graphType: "bar", color: "#fca5a5", altColor: "#fca5a5", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBarHodelBotBg), graphType: "bar", color: "#8b0000", altColor: "#8b0000", min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafBg),            graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6, min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
        {{ type: "wig", format: "bedgraph", url: blob(bafMirBg),         graphType: "points", color: "rgba(80,80,80,0.5)", pointSize: 6, min: -0.6, max: 0.6, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
      ],
    }},

    // ── 4. PURPLE CNV segments (log₂ SEG) ───────────────────────────────────
    {{
      name: " ", type: "seg", format: "seg",
      url: blob(segData), height: 10,
      posColorScale: {{ low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' }},
      negColorScale: {{ low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' }},
    }},

    // ── 5. CNVkit log₂ scatter (.cnr per-target ratios) ─────────────────────
    {{
      type: "merged", name: "CNVkit log\u2082", height: 150,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1,
      tracks: [
        {{ type: "wig", format: "bedgraph", url: blob(cnvkitCnrBg), graphType: "points", color: "#756bb1", pointSize: 10, min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
      ],
    }},

    // ── 6. CNVkit called segments (.call.cns) ────────────────────────────────
    {{
      name: "CNVkit segments", type: "seg", format: "seg",
      url: blob(cnvkitSnsBg), height: 50,
      posColorScale: {{ low: 0.1, high: 1.5, lowColor: 'rgb(255,255,255)', highColor: 'rgb(8,48,107)' }},
      negColorScale: {{ low: -1.5, high: -0.1, lowColor: 'rgb(139,0,0)', highColor: 'rgb(255,255,255)' }},
    }},

    // ── 7. CNVkit gene metrics (.genemetrics.csv) ────────────────────────────
    {{
      type: "merged", name: "CNVkit genemetrics", height: 80,
      min: -2, max: 2, autoscale: false, visibilityWindow: -1,
      tracks: [
        {{ type: "wig", format: "bedgraph", url: blob(cnvkitGenemetricsBg), graphType: "bar", color: "#08306b", altColor: "#8b0000", min: -2, max: 2, autoscale: false, visibilityWindow: -1, baselineColor: "#000000" }},
      ],
    }},

  ]),
}}).then(function (b) {{
  window.igvBrowser = b;
  window.igvReady   = true;
}}).catch(function (e) {{
  window.igvError = String(e);
  console.error(e);
}});

// ── Image export (current view) ─────────────────────────────────────────────
// ppExport is also called by the headless renderer (render_image.py), so the
// downloaded files and the pipeline output come from the same code path.
const ppSampleFile = "{sample_file}";

// Exported images carry the sample ID, purity/ploidy/status and MSI banner from
// the page header, so an image is never separated from the sample it shows.
// Break txt into lines whose rendered width (measured with the same font the SVG
// text uses) fits in `maxPx`, at spaces where possible, hard-splitting any single
// token that is wider than a line, so long sample IDs are never clipped.
function ppWrap(txt, font, maxPx) {{
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = font;
  const fits = function (t) {{ return ctx.measureText(t).width * 1.05 <= maxPx; }};   // 5% safety for font fallback
  const lines = []; let cur = "";
  txt.split(" ").forEach(function (word) {{
    let chars = Array.from(word);        // code points, so a surrogate pair is never split
    while (!fits(chars.join("")) && chars.length > 1) {{
      if (cur) {{ lines.push(cur); cur = ""; }}
      let n = chars.length - 1;
      while (n > 1 && !fits(chars.slice(0, n).join(""))) n--;
      lines.push(chars.slice(0, n).join("")); chars = chars.slice(n);
    }}
    word = chars.join("");
    if (cur && !fits(cur + " " + word)) {{ lines.push(cur); cur = word; }}
    else cur = cur ? cur + " " + word : word;
  }});
  if (cur) lines.push(cur);
  return lines.length ? lines : [""];
}}

function ppSvg() {{
  if (!window.igvBrowser) throw new Error("IGV browser is not ready");
  const NS = "http://www.w3.org/2000/svg";
  const doc  = new DOMParser().parseFromString(window.igvBrowser.toSVG(), "image/svg+xml");
  const root = doc.documentElement;
  const [w, h] = ppSvgSize(root);
  const clean = function (sel) {{ return document.querySelector(sel).textContent.replace(/\\s+/g, " ").trim(); }};
  // The IGV SVG has ruler coordinates but not its navigation bar, so state the genome and
  // locus explicitly: an image must identify what it shows once detached from its filename.
  const loci = window.igvBrowser.currentLoci && window.igvBrowser.currentLoci();
  // igv reports the whole genome as "all:1-<genome length>"; label it only when the range really is
  // the whole genome, so a partial "all:<a>-<b>" view keeps its coordinates.
  const glen = window.igvBrowser.genome && window.igvBrowser.genome.getGenomeLength && window.igvBrowser.genome.getGenomeLength();
  const locusText = [].concat(loci || "all").map(function (l) {{
    const m = /^all(?::(\\d+)-(\\d+))?$/.exec(l);
    return m && (!m[1] || (Number(m[1]) <= 1 && glen && Math.abs(Number(m[2]) - glen) <= 1)) ? "all (whole genome)" : l;
  }}).join(" | ");
  const where = "Genome hg38 \u00b7 Locus: " + locusText;
  const FONT = "Arial, sans-serif", maxPx = w - 24;
  const title = ppWrap(clean("#hdr .ttl"),  "bold 14px " + FONT, maxPx);
  const meta  = ppWrap(clean("#hdr .meta"), "12px " + FONT,      maxPx)
                  .concat(ppWrap(where,     "12px " + FONT,      maxPx));
  const BANNER = 8 + title.length * 18 + meta.length * 16 + 4;

  const body = doc.createElementNS(NS, "g");
  body.setAttribute("transform", "translate(0," + BANNER + ")");
  while (root.firstChild) body.appendChild(root.firstChild);
  const bg = doc.createElementNS(NS, "rect");
  [["x", 0], ["y", 0], ["width", w], ["height", BANNER], ["fill", "#1a2638"]]
    .forEach(function (kv) {{ bg.setAttribute(kv[0], kv[1]); }});
  root.appendChild(bg);
  let y = 8;
  function add(lines, size, step, fill, weight) {{
    lines.forEach(function (txt) {{
      y += step;
      const t = doc.createElementNS(NS, "text");
      [["x", 12], ["y", y - 4], ["font-family", FONT], ["font-size", size],
       ["font-weight", weight], ["fill", fill]].forEach(function (kv) {{ t.setAttribute(kv[0], kv[1]); }});
      t.textContent = txt;
      root.appendChild(t);
    }});
  }}
  add(title, 14, 18, "#e8edf3", "bold");
  add(meta,  12, 16, "#9aafc4", "normal");
  root.appendChild(body);
  root.setAttribute("height", h + BANNER);
  root.setAttribute("viewBox", "0 0 " + w + " " + (h + BANNER));
  return new XMLSerializer().serializeToString(root);
}}

function ppSvgSize(root) {{
  const vb = (root.getAttribute("viewBox") || "").split(/[\\s,]+/).map(Number);
  const w = parseFloat(root.getAttribute("width"))  || vb[2];
  const h = parseFloat(root.getAttribute("height")) || vb[3];
  if (!(w > 0 && h > 0)) throw new Error("SVG has no usable size");
  return [w, h];
}}

function ppPng(scale) {{
  scale = scale || 2;
  const svgText = ppSvg();
  const [w, h] = ppSvgSize(new DOMParser().parseFromString(svgText, "image/svg+xml").documentElement);
  const url = URL.createObjectURL(new Blob([svgText], {{ type: "image/svg+xml;charset=utf-8" }}));
  return new Promise(function (resolve, reject) {{
    const img = new Image();
    img.onload = function () {{
      const c = document.createElement("canvas");
      c.width = Math.round(w * scale); c.height = Math.round(h * scale);
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob(function (blob) {{ blob ? resolve(blob) : reject(new Error("PNG encode failed")); }}, "image/png");
    }};
    img.onerror = function () {{ URL.revokeObjectURL(url); reject(new Error("SVG could not be rasterised")); }};
    img.src = url;
  }});
}}

function ppDownload(blob, name) {{
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function () {{ URL.revokeObjectURL(a.href); }}, 1000);
}}

function ppLocusTag() {{
  const l = window.igvBrowser && window.igvBrowser.currentLoci && window.igvBrowser.currentLoci();
  const t = String(Array.isArray(l) ? l[0] : (l || "view")).replace(/[^A-Za-z0-9._-]+/g, "_");
  return t || "view";
}}

window.ppExport = {{
  svg: ppSvg,
  png: function (scale) {{
    return ppPng(scale).then(function (blob) {{
      return new Promise(function (resolve, reject) {{
        const r = new FileReader();
        r.onload  = function () {{ resolve(String(r.result).split(",")[1]); }};
        r.onerror = function () {{ reject(r.error); }};
        r.readAsDataURL(blob);
      }});
    }});
  }},
}};

document.getElementById("btn-svg").addEventListener("click", function () {{
  try {{
    ppDownload(new Blob([ppSvg()], {{ type: "image/svg+xml" }}), ppSampleFile + "." + ppLocusTag() + ".svg");
  }} catch (e) {{ alert("Could not export SVG: " + e.message); }}
}});
document.getElementById("btn-png").addEventListener("click", function () {{
  ppPng(2).then(function (blob) {{ ppDownload(blob, ppSampleFile + "." + ppLocusTag() + ".png"); }})
          .catch(function (e) {{ alert("Could not export PNG: " + e.message); }});
}});
</script>
</body>
</html>
"""


# ── Rendering ──────────────────────────────────────────────────────────────────

def render_html(inputs: "PlotInputs") -> str:
    """Fill HTML_BARS template from PlotInputs. Empty strings produce empty blob URLs."""
    ploidy = inputs.qc.ploidy

    # Sanitise sample_id for backtick JS literal context (used in seg/CNVkit SEG strings)
    # Also escape < (as \\x3c) so neither </script> nor <!--<script> can end or confuse the inline <script> block
    sample_js = (inputs.sample_id
                 .replace("\\", "\\\\")   # backslash first, or it would un-escape the rest
                 .replace("`", "\\`")
                 .replace("${" , "\\${")
                 .replace("<", "\\x3c"))   # no raw '<': blocks </script> and <!--<script> parser states

    # PURPLE tracks (empty strings if purple is None)
    if inputs.purple is not None:
        cov_log2_bg = coverage_log2_bedgraph(inputs.purple.cn_df)
        bar_bgs     = cn_bars_by_state(inputs.purple.seg_df, ploidy)
        baf_bg      = baf_bedgraph(inputs.purple.baf_df)
        baf_mir_bg  = baf_bedgraph_mirror(inputs.purple.baf_df)
        baf_bar_bgs = baf_seg_bars_by_state(inputs.purple.seg_df, ploidy)
        seg_data    = to_seg(inputs.purple.seg_df, sample_js, ploidy)
    else:
        cov_log2_bg = ""
        bar_bgs     = {k: "" for k, *_ in CN_STATES}
        baf_bg = baf_mir_bg = seg_data = ""
        baf_bar_bgs = {f"{k}_{arm}": "" for k, *_ in CN_STATES for arm in ("top", "bot")}

    # CNVkit tracks (empty strings if dataframe is None)
    cnvkit_cnr_bg = cnr_to_bedgraph(inputs.cnvkit.cnr_df) if inputs.cnvkit.cnr_df is not None else ""
    cnvkit_sns_bg = cns_to_seg(inputs.cnvkit.cns_df, sample_js, ploidy) if inputs.cnvkit.cns_df is not None else ""
    cnvkit_gm_bg  = genemetrics_to_bedgraph(inputs.cnvkit.gm_df) if inputs.cnvkit.gm_df is not None else ""

    # Header values — show "?" when no QC file was loaded
    purity_str = "?" if not inputs.qc.loaded else f"{inputs.qc.purity:.0%}"
    ploidy_str = "?" if not inputs.qc.loaded else f"{inputs.qc.ploidy:.2f}"

    # Sanitise values injected into HTML/JS contexts
    sample_html  = _html.escape(inputs.sample_id)           # used in <title> and <span>
    locus_js     = (inputs.locus
                     .replace("\\", "\\\\")
                     .replace('"', '\\"')
                     .replace("</", "<\\/"))  # prevent </script> injection

    sample_file = re.sub(r"[^A-Za-z0-9._-]+", "_", inputs.sample_id) or "sample"

    return HTML_BARS.format(
        sample               = sample_html,
        sample_file          = sample_file,
        purity               = purity_str,
        ploidy               = ploidy_str,
        status               = _html.escape(inputs.qc.status),
        msi_score            = _html.escape(inputs.msi.score_str),
        msi_status           = _html.escape(inputs.msi.status_str),
        locus                = locus_js,
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
        baf_bar_loss_top_bg  = baf_bar_bgs["loss_top"],
        baf_bar_loss_bot_bg  = baf_bar_bgs["loss_bot"],
        baf_bar_hodel_top_bg = baf_bar_bgs["hodel_top"],
        baf_bar_hodel_bot_bg = baf_bar_bgs["hodel_bot"],
        seg_data             = seg_data,
        cnvkit_cnr_bg        = cnvkit_cnr_bg,
        cnvkit_sns_bg        = cnvkit_sns_bg,
        cnvkit_gm_bg         = cnvkit_gm_bg,
    )


def write_html(inputs: "PlotInputs", output_dir: Path) -> Path:
    """Render HTML and write to {output_dir}/{sample_id}.igv.bars.html."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{inputs.sample_id}.igv.bars.html"
    out.write_text(render_html(inputs))
    return out


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--sample",             required=True,  help="Sample ID")
    ap.add_argument("--purple_tar",         default=None,   help="PURPLE tar.gz archive")
    ap.add_argument("--qc_report",          default=None,   help="QC report TSV")
    ap.add_argument("--cnvkit_cnr",         default=None,   help="CNVkit CNR file")
    ap.add_argument("--cnvkit_call_cns",    default=None,   help="CNVkit called segments")
    ap.add_argument("--cnvkit_genemetrics", default=None,   help="CNVkit genemetrics CSV")
    ap.add_argument("--msi_report",         default=None,   help="MSI report TSV")
    ap.add_argument("--locus",              default="all",  help="Initial IGV locus")
    ap.add_argument("--output_dir",         default=".",    help="Output directory")
    args = ap.parse_args()

    if args.purple_tar is None and args.cnvkit_cnr is None:
        ap.error(
            "At least one of --purple_tar or --cnvkit_cnr must be supplied. "
            "Provide PURPLE outputs, CNVkit outputs, or both."
        )

    purple = load_purple_data(Path(args.purple_tar)) if args.purple_tar else None
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
