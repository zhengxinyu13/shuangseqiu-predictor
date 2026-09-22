"""``shuangseqiu.crowding`` 的测试：拥挤指数能不能复现实测结论。

这批断言把「号码偏好反推」的每一格数字都钉死了。它们同时起两个作用：

- 守住公式本身（基线必须落在 0.990，说明「实际注数 / 理论注数」自洽）；
- 守住读取口径（可用期数必须是「总期数 − 89」—— 2003 年整整 89 期销售额为 0，
  必须被排除；漏排除时基线数值碰巧几乎不变，但可用期数会露馅）。

随开奖变化的期数类数字统一放在 :mod:`expected_data`，刷新方法见那个文件。
"""

from __future__ import annotations

import datetime as dt
import math
import random

import pytest
from expected_data import (
    BASELINE_ACTUAL_WINNERS,
    BASELINE_EXPECTED_WINNERS,
    PERIODS,
    PERIODS_WITHOUT_SALES,
    USABLE_PERIODS,
)

from shuangseqiu import crowding
from shuangseqiu.dataset import DrawRecord

# 拥挤指数逐格数值的容差。刻意比「小数点后三位」宽：
# 这两个指标都是「实际注数之和 / 理论注数之和」，每入库一期就同时改动分子和分母，
# 小组（如 high_red_high_blue、blue[14]）一格能漂 0.002 以上。
# 0.005 既免去每期改数字，又足以抓住真回归——结构性差异本身有 0.6 量级
# （最热 09 是 1.376，最冷 15 是 0.748），公式坏了不可能只偏 0.005。
SHAPE_TOLERANCE = 0.005


def measured(value: float):
    """把实测值包成带容差的近似断言。"""
    return pytest.approx(value, abs=SHAPE_TOLERANCE)


def test_combination_space_constants_are_right() -> None:
    assert crowding.RED_COMBINATIONS == 1_107_568
    assert crowding.TOTAL_COMBINATIONS == 17_721_088
    assert crowding.TICKET_PRICE == 2


def test_baseline_confirms_the_formula(records) -> None:
    """基线是公式的自检项：实际一等奖注数应当非常接近理论注数。"""
    report = crowding.compute_crowding(records)
    assert report.baseline.index == pytest.approx(0.990, abs=0.002)
    assert report.baseline.actual_winners == BASELINE_ACTUAL_WINNERS
    assert report.baseline.expected_winners == pytest.approx(BASELINE_EXPECTED_WINNERS, abs=1)


def test_usable_periods_exclude_the_years_without_sales(records) -> None:
    """哨兵：销售额为 0 的期次必须被排除。

    2003 年共 89 期销售额为 0。曾经因为 ``has_bonus`` 漏写括号，这个过滤被
    静默跳过、可用期数被算成了全部期数。
    """
    report = crowding.compute_crowding(records)
    assert report.usable_periods == USABLE_PERIODS
    without_sales = [record for record in records if not record.has_bonus]
    assert len(without_sales) == PERIODS_WITHOUT_SALES
    assert PERIODS - PERIODS_WITHOUT_SALES == USABLE_PERIODS
    assert {record.year for record in without_sales} == {2003}


def test_blue_indices_match_the_measured_values(records) -> None:
    report = crowding.compute_crowding(records)
    # 最冷的四个与最热的两个
    assert report.blue[15].index == measured(0.748)
    assert report.blue[14].index == measured(0.764)
    assert report.blue[1].index == measured(0.770)
    assert report.blue[16].index == measured(0.804)
    assert report.blue[12].index == measured(1.265)
    assert report.blue[9].index == measured(1.376)
    assert len(report.blue) == 16
    assert all(entry.periods > 150 for entry in report.blue.values())


def test_blue_indices_are_not_sorted_by_number(records) -> None:
    """拥挤度与号码大小无关：最冷是 15，最热是 09，说明这是人的偏好不是数学。"""
    report = crowding.compute_crowding(records)
    ordered = sorted(report.blue, key=lambda number: report.blue[number].index)
    assert ordered[:3] == [15, 14, 1]
    assert ordered[-2:] == [12, 9]


def test_red_shape_indices_match_the_measured_values(records) -> None:
    report = crowding.compute_crowding(records)
    assert report.red_contains_high.index == measured(0.848)
    assert report.red_all_low.index == measured(1.057)
    assert report.sum_high.index == measured(0.806)
    assert report.sum_low.index == measured(1.061)
    assert report.long_run.index == measured(1.119)
    assert report.high_red_high_blue.index == measured(0.748)


def test_big_numbers_are_less_crowded_than_small_ones(records) -> None:
    """大小号两侧应当互补：含大号尾部的偏冷，全小号就偏热。"""
    report = crowding.compute_crowding(records)
    assert report.red_contains_high.index < 1.0 < report.red_all_low.index
    assert report.sum_high.index < 1.0 < report.sum_low.index
    assert (
        report.red_contains_high.periods + report.red_all_low.periods
        == report.usable_periods
    )


def test_long_streaks_are_more_crowded_contradicting_the_folk_claim(records) -> None:
    """3 连号及以上反而偏热（1.119）——「大众会避开连号」的说法站不住。"""
    report = crowding.compute_crowding(records)
    assert report.long_run.index > 1.05


def test_least_crowded_blue_returns_the_coldest_first(records) -> None:
    report = crowding.compute_crowding(records)
    assert report.least_crowded_blue(1) == [15]
    assert report.least_crowded_blue(3) == [15, 14, 1]
    assert len(report.least_crowded_blue(16)) == 16


def test_periods_without_sales_are_skipped_in_a_custom_group() -> None:
    """自造数据：没有销售额的期次既不计入分子也不计入分母，期数也不计。"""
    day = dt.date(2023, 1, 3)
    with_sales = DrawRecord(issue=2023001, date=day, reds=(1, 2, 3, 4, 5, 6), blue=9,
                            sales=crowding.TOTAL_COMBINATIONS * 2, first_winners=1)
    without = DrawRecord(issue=2023002, date=day, reds=(1, 2, 3, 4, 5, 6), blue=9,
                         sales=0, first_winners=99)
    report = crowding.compute_crowding([with_sales, without])
    # 只统计带销售额的那期：实际 1 注 / 理论 1 注 = 1.0
    assert report.usable_periods == 1
    assert report.blue[9].index == pytest.approx(1.0)
    assert report.blue[9].periods == 1
    assert report.baseline.periods == 1


def test_index_is_nan_when_no_period_qualifies() -> None:
    day = dt.date(2023, 1, 3)
    record = DrawRecord(issue=2023001, date=day, reds=(1, 2, 3, 4, 5, 6), blue=1, sales=0)
    report = crowding.compute_crowding([record])
    assert report.usable_periods == 0
    assert report.baseline.index != report.baseline.index  # NaN


def test_empty_input_does_not_explode() -> None:
    report = crowding.compute_crowding([])
    assert report.usable_periods == 0
    assert len(report.blue) == 16


# --------------------------------------------------------------------------
# 「形态均衡」类口径（选号器改用形态条件后新增，2026-09-23）
# --------------------------------------------------------------------------

BALANCE_FIELDS = (
    "odd_even_balanced",
    "big_small_balanced",
    "zone_balanced",
    "one_pair_run",
    "repeat_with_previous",
)


def direction(index: crowding.CrowdingIndex) -> str:
    """按 Poisson 近似判方向：``"cold"`` / ``"hot"`` / ``"flat"``。

    刻意**判方向而不钉数值**：这几项覆盖的期数少（三区 2:2:2 只有 528 期），
    每入库一期就会漂，钉到小数点后会变成每次开奖都要改的负担。
    但方向在 95% 置信度上是稳定的——这才是有价值的结论。
    """
    standard_error = math.sqrt(index.actual_winners) / index.expected_winners
    low = index.index - 1.96 * standard_error
    high = index.index + 1.96 * standard_error
    if high < 1.0:
        return "cold"
    if low > 1.0:
        return "hot"
    return "flat"


def test_balance_dimensions_are_all_populated(records) -> None:
    report = crowding.compute_crowding(records)
    for field in BALANCE_FIELDS:
        index = getattr(report, field)
        assert index.periods > 400, f"{field} 覆盖期数太少：{index.periods}"
        assert index.expected_winners > 0


def test_balance_dimension_directions(records) -> None:
    """选号器那五条形态条件各自的冷热方向——这是整套规则的事实基础。

    实测（2026-09-23，3507 期）：3 奇 3 偶不显著；3 大 3 小与三区 2:2:2
    显著偏热（大众更爱买）；恰好一组二连、与上一期重号 1 个显著偏冷。

    如果这条测试红了，说明 `selector` 文档里那张方向表需要重写——
    别只改数字了事，先看清楚结论是不是真的变了。
    """
    report = crowding.compute_crowding(records)
    assert direction(report.odd_even_balanced) == "flat"
    assert direction(report.big_small_balanced) == "hot"
    assert direction(report.zone_balanced) == "hot"
    assert direction(report.one_pair_run) == "cold"
    assert direction(report.repeat_with_previous) == "cold"


@pytest.mark.parametrize(
    ("field", "measured"),
    [
        ("odd_even_balanced", 1.017),
        ("big_small_balanced", 1.066),
        ("zone_balanced", 1.070),
        ("one_pair_run", 0.961),
        ("repeat_with_previous", 0.974),
    ],
)
def test_balance_dimension_magnitudes_stay_in_band(records, field, measured) -> None:
    """量级留个宽松的锚（±0.02），防止某次改动把口径整个弄错。

    容差故意宽：这些数会随开奖漂，方向由上面那条测试守。
    """
    report = crowding.compute_crowding(records)
    assert getattr(report, field).index == pytest.approx(measured, abs=0.02)


def test_repeat_one_issues_needs_chronological_order(records) -> None:
    """「重号」是唯一依赖时间顺序的口径，所以洗牌后结果必须不变。"""
    shuffled = list(records)
    random.shuffle(shuffled)
    assert crowding.repeat_one_issues(shuffled) == crowding.repeat_one_issues(records)


def test_repeat_one_issues_on_synthetic_data() -> None:
    """自造数据核对定义：与**上一期**红球恰好有 1 个相同。"""
    day = dt.date(2023, 1, 3)
    reds = [
        (1, 2, 3, 4, 5, 6),
        (1, 7, 8, 9, 10, 11),  # 与上期重 1 个（1）→ 命中
        (1, 7, 12, 13, 14, 15),  # 与上期重 2 个（1、7）→ 不命中
        (20, 21, 22, 23, 24, 25),  # 与上期重 0 个 → 不命中
        (20, 26, 27, 28, 29, 30),  # 与上期重 1 个（20）→ 命中
    ]
    sample = [
        crowding.DrawRecord(issue=2023001 + offset, date=day, reds=values, blue=1)
        for offset, values in enumerate(reds)
    ]
    assert crowding.repeat_one_issues(sample) == {2023002, 2023005}
