"""从「奖金与奖池」表反推大众投注偏好——拥挤指数。

这是当前数据集里唯一能间接看见**别人买了什么**的维度：奖池表给出了每期
销售额与一等奖中奖注数，而销售额可以折算出「理论上该中多少注」。

    理论一等奖注数 = (销售额 / 2) ÷ 组合总数

（除以 2 是因为每注 2 元；分母 ``C(33,6) × 16 = 17 721 088`` 是全部单式组合数，
复式票按组合数计价，因此直接除以组合总数即可。）

于是

    拥挤指数 = Σ 实际一等奖注数 / Σ 理论一等奖注数

指数 > 1 说明这一组号码被**更多人买中**，也就是大众的偏好落在这一形态上；
< 1 则是大众偏冷的形态。全表基线应接近 1（实测 0.990），用来验证公式自洽。

**效果边界（必须一起说清楚）**：拥挤度只影响高奖级（一、二等奖）的**分摊**，
三至六等奖是固定奖金，不受影响；一等奖单注还有封顶。它对**中奖概率没有任何影响**，
整体期望收益仍为负。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import comb

from .dataset import (
    BLUE_NUMBERS,
    DrawRecord,
    big_count,
    longest_run,
    odd_count,
    run_lengths,
    zone_counts,
)

RED_COMBINATIONS = comb(33, 6)  # 1 107 568 种红球组合
TOTAL_COMBINATIONS = RED_COMBINATIONS * 16  # 17 721 088 种单式
TICKET_PRICE = 2  # 单注 2 元

HIGH_RED_NUMBERS: tuple[int, ...] = (32, 33)
HIGH_SUM = 120
LOW_SUM = 100
LONG_RUN = 3  # 3 连号及以上

# 「形态均衡」类口径（2026-09-23 新增，供选号器按形态选号时引用实测指数）。
BALANCED_COUNT = 3  # 3 奇 3 偶 / 3 大 3 小
BALANCED_ZONES: tuple[int, int, int] = (2, 2, 2)  # 三区比 2:2:2
ONE_PAIR_RUN = 2  # 恰好一组连号、且这组是二连
REPEAT_WITH_PREVIOUS = 1  # 与上一期重号 1 个


@dataclass(frozen=True)
class CrowdingIndex:
    """一组号码的拥挤程度。"""

    index: float
    actual_winners: int
    expected_winners: float
    periods: int

    @property
    def is_less_crowded(self) -> bool:
        """是否比全表基线更冷（被更少人买中）。"""
        return self.index < 1.0


@dataclass(frozen=True)
class CrowdingReport:
    """整表拥挤度画像。"""

    usable_periods: int
    baseline: CrowdingIndex
    blue: dict[int, CrowdingIndex]
    red_contains_high: CrowdingIndex
    red_all_low: CrowdingIndex
    sum_high: CrowdingIndex
    sum_low: CrowdingIndex
    long_run: CrowdingIndex
    high_red_high_blue: CrowdingIndex
    # 「形态均衡」类（2026-09-23 新增）：选号器改用形态条件后，靠这几项
    # 说明「按这些形态选号是偏冷还是偏热」，而不是凭感觉讲。
    odd_even_balanced: CrowdingIndex
    big_small_balanced: CrowdingIndex
    zone_balanced: CrowdingIndex
    one_pair_run: CrowdingIndex
    repeat_with_previous: CrowdingIndex

    def least_crowded_blue(self, count: int = 3) -> list[int]:
        """按拥挤指数从低到高返回最冷的若干个蓝球（最冷在前）。"""
        ordered = sorted(self.blue.items(), key=lambda item: item[1].index)
        return [number for number, _ in ordered[:count]]


def _index_of(records: Iterable[DrawRecord], predicate: Callable[[DrawRecord], bool]) -> CrowdingIndex:
    """对满足 ``predicate`` 的各期汇总实际 / 理论一等奖注数。"""
    actual = 0
    expected = 0.0
    periods = 0
    for record in records:
        if not record.has_bonus or not predicate(record):
            continue
        actual += record.first_winners or 0
        expected += (record.sales or 0) / TICKET_PRICE / TOTAL_COMBINATIONS
        periods += 1
    return CrowdingIndex(
        index=actual / expected if expected else float("nan"),
        actual_winners=actual,
        expected_winners=expected,
        periods=periods,
    )


def _has_long_run(reds: Sequence[int], length: int = LONG_RUN) -> bool:
    """红球是否存在 ``length`` 连号及以上。"""
    return longest_run(reds) >= length


def repeat_one_issues(records: Sequence[DrawRecord]) -> set[int]:
    """「与上一期红球重号恰好 1 个」的期号集合。

    「重号」只有在时间顺序下才有意义，所以这一项是 :func:`compute_crowding` 里
    **唯一依赖顺序**的口径（其余都与顺序无关）。这里按 ``issue`` 排一遍即可，
    跨年也没问题——``2026001 > 2025154``，整数序就是时间序。
    """
    ordered = sorted(records, key=lambda record: record.issue)
    hits: set[int] = set()
    for previous, current in zip(ordered, ordered[1:]):
        if len(set(current.reds) & set(previous.reds)) == REPEAT_WITH_PREVIOUS:
            hits.add(current.issue)
    return hits


def compute_crowding(
    records: Sequence[DrawRecord],
    high_numbers: Iterable[int] = HIGH_RED_NUMBERS,
    high_sum: int = HIGH_SUM,
    low_sum: int = LOW_SUM,
) -> CrowdingReport:
    """重算整表拥挤指数。

    Args:
        records: 全部开奖记录（除「重号」一项外，顺序无关）。
        high_numbers: 被视为「大号尾部」的红球。
        high_sum: 高和值门槛。
        low_sum: 低和值门槛。

    Returns:
        :class:`CrowdingReport`。销售额为空的期次自动排除（2003 年最初几期）。
    """
    available = [record for record in records if record.has_bonus]
    highs = set(high_numbers)
    repeat_one = repeat_one_issues(records)

    def contains_high(record: DrawRecord) -> bool:
        return any(number in highs for number in record.reds)

    def all_low(record: DrawRecord) -> bool:
        return all(number not in highs for number in record.reds)

    return CrowdingReport(
        usable_periods=len(available),
        baseline=_index_of(available, lambda record: True),
        blue={number: _index_of(available, lambda r, n=number: r.blue == n) for number in BLUE_NUMBERS},
        red_contains_high=_index_of(available, contains_high),
        red_all_low=_index_of(available, all_low),
        sum_high=_index_of(available, lambda r: r.red_sum >= high_sum),
        sum_low=_index_of(available, lambda r: r.red_sum <= low_sum),
        long_run=_index_of(available, lambda r: _has_long_run(r.reds)),
        high_red_high_blue=_index_of(
            available,
            lambda r: contains_high(r) and r.blue >= 13,
        ),
        odd_even_balanced=_index_of(
            available, lambda r: odd_count(r.reds) == BALANCED_COUNT
        ),
        big_small_balanced=_index_of(
            available, lambda r: big_count(r.reds) == BALANCED_COUNT
        ),
        zone_balanced=_index_of(
            available, lambda r: zone_counts(r.reds) == BALANCED_ZONES
        ),
        # 严格读法：恰好一组连号，且这组就是二连（三连以上不算）
        one_pair_run=_index_of(available, lambda r: run_lengths(r.reds) == [ONE_PAIR_RUN]),
        repeat_with_previous=_index_of(available, lambda r: r.issue in repeat_one),
    )
