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
