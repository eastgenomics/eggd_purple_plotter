from pathlib import Path
import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "resources/home/dnanexus/purple_plotter"))

from purple_plotter import (
    coverage_log2_bedgraph, cn_bars_by_state, baf_bedgraph, baf_bedgraph_mirror,
    baf_seg_bars_by_state, to_seg, cnr_to_bedgraph, cns_to_seg, genemetrics_to_bedgraph,
)


def seg_df():
    return pd.DataFrame({
        "chromosome":      ["chr1", "chr2"],
        "start":           [1,      1],
        "end":             [200000, 200000],
        "tumorCopyNumber": [2.1,    3.5],
        "tumorBAF":        [0.52,   0.55],
    })


def cn_df():
    return pd.DataFrame({
        "chromosome":       ["chr1", "chr1"],
        "windowStart":      [1,      100001],
        "windowEnd":        [100000, 200000],
        "windowTumorRatio": [1.02,   0.03],   # 0.03 should be skipped
        "masked":           [0,      0],
    })


def baf_df():
    return pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "position":   [100000, 200000],
        "tumorBAF":   [0.52,   0.48],
    })


def test_coverage_log2_bedgraph_skips_low_ratio():
    bg = coverage_log2_bedgraph(cn_df())
    lines = [l for l in bg.splitlines() if l.strip()]
    # Only the row with windowTumorRatio=1.02 should appear (0.03 skipped)
    assert len(lines) == 1
    assert "chr1\t1\t100000" in lines[0]


def test_cn_bars_by_state_gain():
    bars = cn_bars_by_state(seg_df(), ploidy=2.0)
    # chr2 CN=3.5, ploidy=2.0, log2=0.807 → gain
    assert any("chr2" in l for l in bars["gain"].splitlines())
    # chr1 CN=2.1, ploidy=2.0, log2=0.07 → diploid
    assert any("chr1" in l for l in bars["diploid"].splitlines())


def test_baf_bedgraph_values():
    bg = baf_bedgraph(baf_df())
    lines = bg.splitlines()
    assert len(lines) == 2
    # value = tumorBAF - 0.5
    assert "0.0200" in lines[0]   # 0.52 - 0.5


def test_baf_bedgraph_mirror_values():
    bg = baf_bedgraph_mirror(baf_df())
    lines = bg.splitlines()
    assert "-0.0200" in lines[0]  # 0.5 - 0.52


def test_to_seg_header():
    seg = to_seg(seg_df(), "SAMPLE1", ploidy=2.0)
    assert seg.startswith("SampleID\tChromosome")
    lines = [l for l in seg.splitlines() if "SAMPLE1" in l]
    assert len(lines) == 2


def test_cnr_to_bedgraph():
    cnr = pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "start": [1, 501], "end": [500, 1000],
        "log2": [0.12, float("inf")],  # inf should be skipped
    })
    bg = cnr_to_bedgraph(cnr)
    lines = [l for l in bg.splitlines() if l.strip()]
    assert len(lines) == 1
    assert "0.1200" in lines[0]


def test_genemetrics_to_bedgraph_skips_nonfinite():
    gm = pd.DataFrame({
        "chromosome": ["chr1", "chr1"],
        "start": [1, 501], "end": [500, 1000],
        "log2": [0.12, float("nan")],
    })
    bg = genemetrics_to_bedgraph(gm)
    lines = [l for l in bg.splitlines() if l.strip()]
    assert len(lines) == 1
