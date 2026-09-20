"""统计计算模块的测试。

合成小样本用来验证每个算法本身是否算对；
真实可信段用来验证整体规模与内部自洽性。
"""

from __future__ import annotations

import pytest

from shuangseqiu.quality import build_quality_report
from shuangseqiu.stats import (
    ac_value,
    big_counts,
    blue_frequencies,
    build_summary,
    chi_square_uniformity,
    consecutive_groups,
    current_omission,
    histogram,
    max_omission,
    odd_counts,
    red_frequencies,
    repeat_counts,
    zone_counts,
)


# ------------------------------------------------------------------- 频率


def test_red_frequencies_count_rate_and_deviation(make_draw):
    draws = [
        make_draw(1, (1, 2, 3, 4, 5, 6), 1),
        make_draw(2, (1, 2, 3, 4, 5, 7), 2),
    ]
    table = {item.number: item for item in red_frequencies(draws)}

    assert table[1].count == 2
    assert table[1].rate == 1.0
    assert table[6].count == 1
    assert table[7].count == 1
    assert table[33].count == 0
    # 期望次数 = 期数 x 6/33
    assert table[1].deviation == pytest.approx(2 - 2 * 6 / 33)
    assert len(red_frequencies(draws)) == 33


def test_blue_frequencies(make_draw):
    draws = [
        make_draw(1, blue=16),
        make_draw(2, blue=16),
        make_draw(3, blue=1),
    ]
    table = {item.number: item for item in blue_frequencies(draws)}

    assert table[16].count == 2
    assert table[1].count == 1
    assert table[2].count == 0
    assert table[16].rate == pytest.approx(2 / 3)
    assert len(blue_frequencies(draws)) == 16


# ------------------------------------------------------------------- 遗漏


def test_current_omission_measures_from_newest(make_draw):
    draws = [
        make_draw(3, (10, 20, 30, 31, 32, 33), 1),  # 最新一期，含 10
        make_draw(2, (11, 20, 30, 31, 32, 33), 1),
        make_draw(1, (10, 20, 30, 31, 32, 33), 1),  # 最老一期
    ]
    omission = current_omission(draws, (10, 11, 12), lambda draw: draw.reds)

    assert omission[10] == 0  # 最新一期就出现过
    assert omission[11] == 1  # 上一期出现过
    assert omission[12] == 3  # 整个区间都没出现，记总期数


def test_max_omission_finds_longest_streak(make_draw):
    draws = [
        make_draw(4, (10, 20, 30, 31, 32, 33), 1),
        make_draw(3, (11, 20, 30, 31, 32, 33), 1),
        make_draw(2, (12, 20, 30, 31, 32, 33), 1),
        make_draw(1, (10, 20, 30, 31, 32, 33), 1),
    ]
    # 按时间正序是 001(有10) -> 002(无) -> 003(无) -> 004(有10)，最长连续缺席 2 期
    assert max_omission(draws, (10,), lambda draw: draw.reds) == {10: 2}


def test_max_omission_counts_trailing_streak(make_draw):
    draws = [
        make_draw(3, (11, 20, 30, 31, 32, 33), 1),
        make_draw(2, (12, 20, 30, 31, 32, 33), 1),
        make_draw(1, (10, 20, 30, 31, 32, 33), 1),
    ]
    # 10 只在最老一期出现，之后连续缺席 2 期，理应计入最大遗漏
    assert max_omission(draws, (10,), lambda draw: draw.reds) == {10: 2}


# ----------------------------------------------------------------- 形态


@pytest.mark.parametrize(
    ("reds", "expected"),
    [
        ((1, 2, 3, 4, 5, 6), 1),
        ((6, 7, 8, 20, 21, 30), 2),
        ((1, 3, 5, 7, 9, 11), 0),
        ((1, 2, 10, 20, 21, 22), 2),
        ((1, 2, 3, 20, 21, 22), 2),
    ],
)
def test_consecutive_groups(reds, expected):
    assert consecutive_groups(reds) == expected


def test_ac_value_of_arithmetic_sequence_is_zero():
    assert ac_value((1, 2, 3, 4, 5, 6)) == 0


def test_ac_value_counts_distinct_differences():
    # (1,2,3,4,5,7) 的两两差值集合为 {1,2,3,4,5,6}，共 6 个 -> 6 - (6 - 1) = 1
    assert ac_value((1, 2, 3, 4, 5, 7)) == 1


def test_repeat_counts_compares_with_previous_period(make_draw):
    draws = [
        make_draw(2, (1, 2, 3, 4, 5, 6), 1),
        make_draw(1, (1, 2, 3, 7, 8, 9), 1),
    ]
    assert repeat_counts(draws) == [3]


def test_odd_and_big_counts(make_draw):
    draw = make_draw(1, (1, 2, 3, 4, 5, 6), 1)
    assert odd_counts([draw]) == [3]  # 1、3、5
    assert big_counts([draw]) == [0]


def test_big_count_treats_16_as_small(make_draw):
    draw = make_draw(1, (16, 17, 18, 30, 31, 33), 1)
    assert big_counts([draw]) == [5]  # 只有 16 算小号
    assert odd_counts([draw]) == [3]  # 17、31、33


def test_zone_counts(make_draw):
    draw = make_draw(1, (1, 11, 12, 22, 23, 33), 1)
    assert zone_counts(draw) == (2, 2, 2)


# ---------------------------------------------------------------- 分布桶


def test_histogram_buckets():
    result = histogram([21, 25, 30, 35, 100], low=20, high=190, width=10)

    assert result["labels"][0] == "20-29"
    assert result["counts"][0] == 2  # 21、25
    assert result["counts"][1] == 2  # 30、35
    assert result["counts"][8] == 1  # 100
    assert sum(result["counts"]) == 5


def test_chi_square_on_perfectly_uniform_data():
    result = chi_square_uniformity([10] * 33)

    assert result["statistic"] == pytest.approx(0.0)
    assert result["p_value"] == pytest.approx(1.0)
    assert result["dof"] == 32


def test_chi_square_detects_skew():
    skewed = [100] + [1] * 32
    assert chi_square_uniformity(skewed)["p_value"] < 0.001


# ------------------------------------------------------------- 汇总结构


def test_build_summary_structure(make_draw):
    draws = [make_draw(index) for index in range(1, 4)]
    summary = build_summary(draws)

    assert summary["meta"]["periods"] == 3
    assert len(summary["red_frequency"]) == 33
    assert len(summary["blue_frequency"]) == 16
    assert len(summary["sums"]["values"]) == 3
    assert sum(summary["sums"]["values"]) == 3 * 21  # 每期都是 1+2+3+4+5+6
    assert sum(item["count"] for item in summary["red_frequency"]) == 3 * 6
    assert sum(item["count"] for item in summary["blue_frequency"]) == 3
    assert len(summary["heatmap"]["matrix"]) == len(summary["meta"]["years"])


# --------------------------------------------------------- 真实数据断言

# 全量数据集的结构校验结论：3505 期全部可信，无一剔除。
EXPECTED_PERIODS = 3505


def test_real_trusted_segment_keeps_every_record(draws):
    report = build_quality_report(draws)

    assert report.total_records == EXPECTED_PERIODS
    assert report.trusted_count == EXPECTED_PERIODS
    assert report.suspect_count == 0


def test_real_trusted_summary_is_self_consistent(draws):
    report = build_quality_report(draws)
    summary = build_summary(report.trusted_draws)
    meta = summary["meta"]

    assert meta["periods"] == EXPECTED_PERIODS
    assert meta["first_issue"] == "2026108期"  # 文件是倒序，首行即最新一期
    assert meta["last_issue"] == "2003001期"  # 末行即历史首期
    assert meta["years"] == list(range(2003, 2027))

    assert sum(item["count"] for item in summary["red_frequency"]) == EXPECTED_PERIODS * 6
    assert sum(item["count"] for item in summary["blue_frequency"]) == EXPECTED_PERIODS
    assert len(summary["sums"]["values"]) == EXPECTED_PERIODS
    assert len(summary["heatmap"]["matrix"]) == len(meta["years"])


def test_real_trusted_omission_is_consistent(draws):
    report = build_quality_report(draws)
    summary = build_summary(report.trusted_draws)

    # 最新一期恰好开出 6 个不同红球，故"当前遗漏为 0"的号码应恰好 6 个
    red_zero = sum(1 for value in summary["red_omission_current"].values() if value == 0)
    blue_zero = sum(1 for value in summary["blue_omission_current"].values() if value == 0)
    assert red_zero == 6
    assert blue_zero == 1

    for value in summary["red_omission_current"].values():
        assert 0 <= value <= EXPECTED_PERIODS
    for value in summary["blue_omission_current"].values():
        assert 0 <= value <= EXPECTED_PERIODS


def test_real_sum_histogram_covers_every_period(draws):
    """和值/跨度分桶必须不重不漏地装下所有期数，否则图表会少算。"""
    summary = build_summary(build_quality_report(draws).trusted_draws)

    assert sum(summary["sums"]["hist"]["counts"]) == EXPECTED_PERIODS
    assert sum(summary["spans"]["hist"]["counts"]) == EXPECTED_PERIODS


def test_real_shape_distributions_cover_every_period(draws):
    summary = build_summary(build_quality_report(draws).trusted_draws)

    for key in ("odd_even", "big_small", "consecutive", "zones", "ac_values"):
        assert sum(summary[key]["counts"]) == EXPECTED_PERIODS, f"{key} 分布未覆盖全部期数"
    # 重号是"与上一期比较"，最新一期没有上一期，故比期数少 1
    assert sum(summary["repeats"]["counts"]) == EXPECTED_PERIODS - 1


def test_real_data_has_four_or_more_repeats(draws):
    """回归测试：重号桶必须覆盖到 6，否则高频重号会被静默丢掉。

    旧实现只开 0~3 共 4 个桶，把 4 个及以上重号的期数直接从分布里吞掉了。
    """
    summary = build_summary(build_quality_report(draws).trusted_draws)
    buckets = dict(zip(summary["repeats"]["labels"], summary["repeats"]["counts"]))

    assert "6 个重号" in buckets, "重号分桶未覆盖到上限 6"
    assert buckets["4 个重号"] > 0, "历史上确实出现过 4 个重号，桶里不该是 0"
