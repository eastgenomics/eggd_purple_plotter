"""eggd_purple_plotter — Generate a self-contained IGV bars HTML from PURPLE/CNVkit outputs."""
import argparse
import gzip
import io
import math
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

def load_purple_data(purple_tar: Path, sample_id: str) -> PurpleData:
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
    return QCData(
        purity = float(row["purity"]),
        ploidy = float(row["ploidy"]),
        status = str(row.get("status", "UNKNOWN")),
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
