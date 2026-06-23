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
