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
