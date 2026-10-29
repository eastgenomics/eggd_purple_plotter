"""eggd_purple_plotter — Generate an IGV bars HTML viewer from PURPLE/CNVkit outputs.

igv.js and all track data are embedded in the HTML; the hg38 genome and annotation are
fetched from igv.org / UCSC when the file is opened in a browser.
"""
import argparse
import base64
import functools
import gzip
import hashlib
import html as _html
import io
import json
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


class InputError(ValueError):
    """A supplied input file cannot be used as it is."""


def load_qc_data(qc_report: "Path | None") -> QCData:
    """Parse purity, ploidy and (optional) fit status from the first row of a tab-separated report.

    Accepts PURPLE's final-pass ``{sample}.purple.purity.tsv`` or any compatible report with
    numeric ``purity`` and ``ploidy`` columns. ``status`` is the fit status supplied by that
    file (e.g. NORMAL, NO_TUMOR), not a QC verdict; missing or blank → "UNKNOWN".
    Returns defaults (``loaded=False``) only when no report was supplied. A supplied file that is
    missing, empty, or lacks numeric purity/ploidy raises InputError — it never silently becomes
    the no-report fallback.
    """
    if qc_report is None:
        return QCData()
    path = Path(qc_report)
    if not path.is_file():
        raise InputError(f"qc_report {path} does not exist")
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"qc_report {path.name} cannot be read: {e}") from None
    if not lines:
        raise InputError(f"qc_report {path.name} is empty")
    # Plain tab splitting (no quoting rules): a value can never shift under another header,
    # which pandas allows when a row has more fields than the header.
    header = [h.strip() for h in lines[0].split("\t")]
    missing = [c for c in ("purity", "ploidy") if c not in header]
    if missing:
        raise InputError(f"qc_report {path.name} has no {'/'.join(missing)} column "
                         f"(columns: {', '.join(header)}); expected a tab-separated "
                         "file such as PURPLE's purity.tsv")
    dupes = sorted({c for c in ("purity", "ploidy", "status") if header.count(c) > 1})
    if dupes:
        raise InputError(f"qc_report {path.name} has more than one {'/'.join(dupes)} column")
    if len(lines) < 2:
        raise InputError(f"qc_report {path.name} has a header but no data row")
    fields = lines[1].split("\t")
    if len(fields) != len(header):
        raise InputError(f"qc_report {path.name}: first data row has {len(fields)} fields "
                         f"but the header has {len(header)}")
    row = dict(zip(header, (f.strip() for f in fields)))
    values = {}
    for col in ("purity", "ploidy"):
        try:
            values[col] = float(row[col])
        except ValueError:
            values[col] = float("nan")
        if not math.isfinite(values[col]):
            raise InputError(f"qc_report {path.name}: {col} value {row[col]!r} is not a number")
    if not 0.0 <= values["purity"] <= 1.0:
        raise InputError(f"qc_report {path.name}: purity {values['purity']} is outside 0-1")
    if values["ploidy"] < 0.1:
        # Segment conversion clamps the denominator at 0.1; a smaller value could not be drawn as given.
        raise InputError(f"qc_report {path.name}: ploidy {values['ploidy']} is below 0.1")
    val = row.get("status", "")
    status = val if val else "UNKNOWN"
    return QCData(purity=values["purity"], ploidy=values["ploidy"], status=status, loaded=True)


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
    """CNVkit genemetrics (tab-separated .genemetrics.tsv / .csv) → bedgraph of log₂ values."""
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
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>IGV bars — {sample}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; }}
#hdr {{ background: #1a2638; color: #e8edf3; padding: 10px 18px;
        display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
#hdr .ttl  {{ font-size: 15px; font-weight: 600; }}
#hdr .meta {{ font-size: 12px; color: #9aafc4; }}
#hdr .warn {{ font-size: 12px; color: #1a2638; background: #ffd666; border-radius: 3px; padding: 1px 6px; }}
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
    Purity {purity} &nbsp;·&nbsp; Ploidy {ploidy} &nbsp;·&nbsp;
    <span title="Fit status reported in the supplied purity/ploidy file. It is not a QC pass/fail assessment.">Fit status: {status} (not a QC result)</span>
    &nbsp;·&nbsp; MSI {msi_score} &nbsp;·&nbsp; {msi_status}
  </span>{ploidy_warning}
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
    Bars y: log&#x2082;(CN/ploidy); 0&#xa0;=&#xa0;CN equal to the ploidy &nbsp;&#xb7;&nbsp;
    CNVkit y: log&#x2082; ratio; 0&#xa0;=&#xa0;diploid &nbsp;&#xb7;&nbsp;
    BAF y: 0&#x2013;1
  </span>
</div>

<script type="application/json" id="pp-data">{data_json}</script>
<!--
{igv_licence}-->
<script>{igv_js}</script>
<script>{viewer_js}</script>
</body>
</html>
"""


# ── Rendering ──────────────────────────────────────────────────────────────────

PKG_DIR = Path(__file__).resolve().parent
ASSETS  = PKG_DIR / "assets"

# igv.js 3.8.3 (byte-identical to https://igv.org/web/release/3.8.3/dist/igv.min.js), embedded in
# every viewer so no third-party script is loaded. Checked at load time so a wrong or partial
# asset fails loudly. On a version bump, change the file, this hash and IGV_JS_VERSION
# (see assets/README.md).
IGV_JS_VERSION = "3.8.3"
IGV_JS_SHA256  = "acf4ee99cdc91be884203930db9c395bc86267552933b483da0b609c16ea05aa"

# Origins the viewer may fetch from: blob: track data embedded in the page, igv.org
# (genomes3.json, fasta index, cytobands, aliases, gene search) and UCSC (hg38 2bit sequence,
# RefSeq annotation). See README "Security" for how each directive was established.
CSP_CONNECT_SRC = ["blob:", "https://igv.org", "https://hgdownload.soe.ucsc.edu"]


def _guard_inline_script(name: str, text: str) -> str:
    """Refuse script text that the HTML parser would end or misread inside an inline <script>."""
    low = text.lower()
    for bad in ("</script", "<script", "<!--"):
        if bad in low:
            raise RuntimeError(f"{name} contains {bad!r} and cannot be inlined in a <script> element")
    return text


@functools.lru_cache(maxsize=1)
def igv_js() -> str:
    """The bundled igv.min.js, verified against IGV_JS_SHA256."""
    data = (ASSETS / "igv.min.js").read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != IGV_JS_SHA256:
        raise RuntimeError(f"assets/igv.min.js has SHA-256 {got}, expected {IGV_JS_SHA256}")
    return _guard_inline_script("igv.min.js", data.decode("utf-8"))


@functools.lru_cache(maxsize=1)
def igv_licence() -> str:
    """igv.js MIT licence text, reproduced in every viewer as the licence requires."""
    text = (ASSETS / "igv.js.LICENSE").read_text(encoding="utf-8")
    if "--" in text or ">" in text:
        raise RuntimeError("igv.js.LICENSE contains characters that would end the HTML comment")
    return (f"igv.js {IGV_JS_VERSION} (https://github.com/igvteam/igv.js), embedded below.\n"
            "It includes DOMPurify (Apache-2.0 / MPL-2.0); its notice is inside the script.\n\n"
            + text)


@functools.lru_cache(maxsize=1)
def viewer_js() -> str:
    """The fixed viewer code (viewer.js): identical for every sample."""
    return _guard_inline_script("viewer.js", (PKG_DIR / "viewer.js").read_text(encoding="utf-8"))


def script_hash(text: str) -> str:
    """CSP source expression for an inline script: 'sha256-<base64 of the UTF-8 text>'."""
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def script_hashes() -> "list[str]":
    """Hashes of the two executable scripts every viewer contains (igv.js, viewer.js)."""
    return [script_hash(igv_js()), script_hash(viewer_js())]


def content_security_policy() -> str:
    """The CSP carried in every viewer's <meta>. Only the two hashed scripts may run."""
    return "; ".join([
        "default-src 'none'",
        "script-src " + " ".join(f"'{h}'" for h in script_hashes()),
        "connect-src " + " ".join(CSP_CONNECT_SRC),
        "img-src data: blob:",
        "style-src 'unsafe-inline'",       # igv.js injects <style>; the legend uses style="" (styles cannot run code)
        "base-uri 'none'",
        "form-action 'none'",
        "object-src 'none'",
    ])


def json_for_html(obj) -> str:
    """JSON text that is safe inside <script type="application/json">.

    ensure_ascii escapes all non-ASCII (including U+2028/U+2029); < > & are escaped so the
    text can never end the element or start a comment. The block is never executed.
    """
    text = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# Shown (and carried into exported images) when no purity/ploidy report was supplied: the
# CN/ploidy segment tracks then use the QCData default ploidy, which is an assumption.
PLOIDY_ASSUMED_WARNING = (
    '\n  <span class="warn">No purity/ploidy report supplied: ploidy {ploidy} is assumed for the '
    'CN/ploidy segment tracks. This is not a measured ploidy.</span>'
)


def render_html(inputs: "PlotInputs") -> str:
    """Fill HTML_BARS from PlotInputs. Empty track strings produce no track (see viewer.js)."""
    ploidy = inputs.qc.ploidy
    sample = inputs.sample_id      # raw: it only ever reaches the JSON block (encoded) or HTML (escaped)
    # SEG is tab/newline-delimited, and the sample name is a SEG column: a tab, CR or LF in the
    # ID would add or corrupt rows. JSON encoding cannot prevent that, so replace them here.
    seg_sample = re.sub(r"[\t\r\n]", "_", sample)

    # PURPLE tracks (empty strings if purple is None)
    if inputs.purple is not None:
        cov_log2_bg = coverage_log2_bedgraph(inputs.purple.cn_df)
        bar_bgs     = cn_bars_by_state(inputs.purple.seg_df, ploidy)
        baf_bg      = baf_bedgraph(inputs.purple.baf_df)
        baf_mir_bg  = baf_bedgraph_mirror(inputs.purple.baf_df)
        baf_bar_bgs = baf_seg_bars_by_state(inputs.purple.seg_df, ploidy)
        seg_data    = to_seg(inputs.purple.seg_df, seg_sample, ploidy)
    else:
        cov_log2_bg = ""
        bar_bgs     = {k: "" for k, *_ in CN_STATES}
        baf_bg = baf_mir_bg = seg_data = ""
        baf_bar_bgs = {f"{k}_{arm}": "" for k, *_ in CN_STATES for arm in ("top", "bot")}

    # CNVkit tracks (empty strings if dataframe is None)
    cnvkit_cnr_bg = cnr_to_bedgraph(inputs.cnvkit.cnr_df) if inputs.cnvkit.cnr_df is not None else ""
    cnvkit_sns_bg = cns_to_seg(inputs.cnvkit.cns_df, seg_sample, ploidy) if inputs.cnvkit.cns_df is not None else ""
    cnvkit_gm_bg  = genemetrics_to_bedgraph(inputs.cnvkit.gm_df) if inputs.cnvkit.gm_df is not None else ""

    # Header values — show "?" when no QC file was loaded
    purity_str = "?" if not inputs.qc.loaded else f"{inputs.qc.purity:.0%}"
    ploidy_str = "?" if not inputs.qc.loaded else f"{inputs.qc.ploidy:.2f}"
    status_str = "?" if not inputs.qc.loaded else inputs.qc.status
    ploidy_warning = "" if inputs.qc.loaded else PLOIDY_ASSUMED_WARNING.format(ploidy=f"{ploidy:.1f}")

    # All per-sample values the viewer code reads, in one non-executable JSON block.
    data = {
        "locus":                inputs.locus,
        "sample_file":          re.sub(r"[^A-Za-z0-9._-]+", "_", sample) or "sample",
        "cov_log2_bg":          cov_log2_bg,
        "bar_amp_bg":           bar_bgs["amp"],
        "bar_gain_bg":          bar_bgs["gain"],
        "bar_loss_bg":          bar_bgs["loss"],
        "bar_hodel_bg":         bar_bgs["hodel"],
        "baf_bg":               baf_bg,
        "baf_mir_bg":           baf_mir_bg,
        "baf_bar_amp_top_bg":   baf_bar_bgs["amp_top"],
        "baf_bar_amp_bot_bg":   baf_bar_bgs["amp_bot"],
        "baf_bar_gain_top_bg":  baf_bar_bgs["gain_top"],
        "baf_bar_gain_bot_bg":  baf_bar_bgs["gain_bot"],
        "baf_bar_loss_top_bg":  baf_bar_bgs["loss_top"],
        "baf_bar_loss_bot_bg":  baf_bar_bgs["loss_bot"],
        "baf_bar_hodel_top_bg": baf_bar_bgs["hodel_top"],
        "baf_bar_hodel_bot_bg": baf_bar_bgs["hodel_bot"],
        "seg_data":             seg_data,
        "cnvkit_cnr_bg":        cnvkit_cnr_bg,
        "cnvkit_sns_bg":        cnvkit_sns_bg,
        "cnvkit_gm_bg":         cnvkit_gm_bg,
    }

    return HTML_BARS.format(
        csp          = _html.escape(content_security_policy(), quote=False),   # never contains '"' (checked in tests)
        sample       = _html.escape(sample),                 # <title> and header <span>
        purity       = purity_str,
        ploidy       = ploidy_str,
        status       = _html.escape(status_str),
        ploidy_warning = ploidy_warning,
        msi_score    = _html.escape(inputs.msi.score_str),
        msi_status   = _html.escape(inputs.msi.status_str),
        data_json    = json_for_html(data),
        igv_licence  = igv_licence(),
        igv_js       = igv_js(),
        viewer_js    = viewer_js(),
    )


def check_sample_id_for_filename(sample_id: str) -> None:
    """The output is {sample_id}.igv.bars.html (here and in src/code.sh), so the ID must be a
    plain file name: no path separators, NUL, or "." / ".."."""
    if not sample_id or sample_id in (".", "..") or any(c in sample_id for c in "/\\\0"):
        raise ValueError(f"sample ID {sample_id!r} cannot be used as a file name "
                         "(it must not be empty, '.', '..', or contain '/', '\\' or NUL)")


def write_html(inputs: "PlotInputs", output_dir: Path) -> Path:
    """Render HTML and write to {output_dir}/{sample_id}.igv.bars.html."""
    check_sample_id_for_filename(inputs.sample_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{inputs.sample_id}.igv.bars.html"
    out.write_text(render_html(inputs), encoding="utf-8")
    return out


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--sample",             default=None,   help="Sample ID (required unless --print-script-hashes)")
    ap.add_argument("--purple_tar",         default=None,   help="PURPLE tar.gz archive")
    ap.add_argument("--qc_report",          default=None,   help="Purity/ploidy TSV: PURPLE purity.tsv or compatible (purity, ploidy, optional fit status)")
    ap.add_argument("--cnvkit_cnr",         default=None,   help="CNVkit CNR file")
    ap.add_argument("--cnvkit_call_cns",    default=None,   help="CNVkit called segments")
    ap.add_argument("--cnvkit_genemetrics", default=None,   help="CNVkit genemetrics, tab-separated (.genemetrics.tsv, or the older .genemetrics.csv)")
    ap.add_argument("--msi_report",         default=None,   help="MSI report TSV")
    ap.add_argument("--locus",              default="all",  help="Initial IGV locus")
    ap.add_argument("--output_dir",         default=".",    help="Output directory")
    ap.add_argument("--print-script-hashes", action="store_true",
                    help="Print the CSP sha256 hashes of the two scripts every viewer contains, then exit")
    args = ap.parse_args()

    if args.print_script_hashes:
        for h in script_hashes():
            print(h)
        return
    if not args.sample:
        ap.error("--sample is required")
    try:
        check_sample_id_for_filename(args.sample)
    except ValueError as e:
        ap.error(str(e))

    if args.purple_tar is None and args.cnvkit_cnr is None:
        ap.error(
            "At least one of --purple_tar or --cnvkit_cnr must be supplied. "
            "Provide PURPLE outputs, CNVkit outputs, or both."
        )

    purple = load_purple_data(Path(args.purple_tar)) if args.purple_tar else None
    try:
        qc = load_qc_data(Path(args.qc_report) if args.qc_report is not None else None)
    except InputError as e:
        ap.exit(1, f"ERROR: {e}\n")
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
