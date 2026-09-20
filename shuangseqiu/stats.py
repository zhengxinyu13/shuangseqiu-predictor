"""探索性统计：号码频率、形态分布、遗漏与随机性检验。

约定：传入的 ``draws`` 与数据文件顺序一致（最新一期在前）。
凡是涉及"遗漏""与上一期比较"的计算，内部都会自行处理时间方向。

所有函数都不修改入参，返回纯 Python 结构，便于直接序列化成 JSON
供 HTML 报告使用。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Sequence

from scipy import stats as scipy_stats

from shuangseqiu.data import (
    BLUE_BALL_MAX,
    BLUE_BALL_MIN,
    RED_BALL_COUNT,
    RED_BALL_MAX,
    RED_BALL_MIN,
    Draw,
)

RED_NUMBERS: tuple[int, ...] = tuple(range(RED_BALL_MIN, RED_BALL_MAX + 1))
BLUE_NUMBERS: tuple[int, ...] = tuple(range(BLUE_BALL_MIN, BLUE_BALL_MAX + 1))

# 三区划分：一区 1-11、二区 12-22、三区 23-33
ZONE_BOUNDS: tuple[tuple[int, int], ...] = ((1, 11), (12, 22), (23, 33))

# 大小划分：红球 01-16 记作小号，17-33 记作大号（双色球标准口径）
# 与 data/scripts/build_dataset.py 的「大小比」列保持同一口径
BIG_NUMBER_MIN = 17

# 红球和值的理论取值区间：最小 1+2+3+4+5+6=21，最大 28+29+30+31+32+33=183
SUM_MIN = 21
SUM_MAX = 183

# 直方图分桶宽度
HIST_BIN = 10


@dataclass(frozen=True)
class Frequency:
    """单个号码的出现频次。"""

    number: int
    count: int
    rate: float
    deviation: float


def _reds_of(draw: Draw) -> Iterable[int]:
    return draw.reds


def _blues_of(draw: Draw) -> Iterable[int]:
    return (draw.blue,)


def _frequency_table(
    draws: Sequence[Draw],
    universe: tuple[int, ...],
    getter: Callable[[Draw], Iterable[int]],
    per_draw_expected: float,
) -> list[Frequency]:
    """按号码统计出现次数，并按均匀随机假设给出期望值与偏差。"""
    periods = len(draws)
    counts: Counter[int] = Counter()
    for draw in draws:
        counts.update(getter(draw))
    expected = per_draw_expected * periods
    return [
        Frequency(
            number=number,
            count=counts.get(number, 0),
            rate=(counts.get(number, 0) / periods) if periods else 0.0,
            deviation=counts.get(number, 0) - expected,
        )
        for number in universe
    ]


def red_frequencies(draws: Sequence[Draw]) -> list[Frequency]:
    """红球频率表。均匀随机下每个红球的出现概率为 6/33。"""
    return _frequency_table(draws, RED_NUMBERS, _reds_of, RED_BALL_COUNT / len(RED_NUMBERS))


def blue_frequencies(draws: Sequence[Draw]) -> list[Frequency]:
    """蓝球频率表。均匀随机下每个蓝球的出现概率为 1/16。"""
    return _frequency_table(draws, BLUE_NUMBERS, _blues_of, 1 / len(BLUE_NUMBERS))


def current_omission(
    draws: Sequence[Draw],
    universe: tuple[int, ...],
    getter: Callable[[Draw], Iterable[int]],
) -> dict[int, int]:
    """当前遗漏：从最新一期往前数，该号码已连续多少期未出现。

    最新一期出现过记 0；若整个区间内从未出现，记为总期数。
    """
    first_seen: dict[int, int] = {}
    for position, draw in enumerate(draws):
        for number in getter(draw):
            first_seen.setdefault(number, position)
    return {number: first_seen.get(number, len(draws)) for number in universe}


def max_omission(
    draws: Sequence[Draw],
    universe: tuple[int, ...],
    getter: Callable[[Draw], Iterable[int]],
) -> dict[int, int]:
    """历史最大遗漏：该号码曾连续未出现的最大期数（含末尾未出现段）。"""
    result = {number: 0 for number in universe}
    running = {number: 0 for number in universe}
    for draw in reversed(draws):  # 按时间正序扫描
        appeared = set(getter(draw))
        for number in universe:
            if number in appeared:
                result[number] = max(result[number], running[number])
                running[number] = 0
            else:
                running[number] += 1
    for number in universe:
        result[number] = max(result[number], running[number])
    return result


def sum_values(draws: Sequence[Draw]) -> list[int]:
    """每期红球和值。"""
    return [sum(draw.reds) for draw in draws]


def span_values(draws: Sequence[Draw]) -> list[int]:
    """每期跨度：红球最大值减最小值。"""
    return [max(draw.reds) - min(draw.reds) for draw in draws]


def odd_counts(draws: Sequence[Draw]) -> list[int]:
    """每期红球中的奇数个数。"""
    return [sum(1 for number in draw.reds if number % 2 == 1) for draw in draws]


def big_counts(draws: Sequence[Draw]) -> list[int]:
    """每期红球中的大号个数（不小于 17 记为大号，即 01-16 为小号）。"""
    return [sum(1 for number in draw.reds if number >= BIG_NUMBER_MIN) for draw in draws]


def zone_counts(draw: Draw) -> tuple[int, int, int]:
    """某一期在三个区间（1-11 / 12-22 / 23-33）内的红球个数。"""
    return tuple(
        sum(1 for number in draw.reds if low <= number <= high) for low, high in ZONE_BOUNDS
    )  # type: ignore[return-value]


def consecutive_groups(reds: Sequence[int]) -> int:
    """红球中的连号组数。如 6,7,8,20,21,30 记为 2 组。"""
    groups = 0
    run = 1
    for previous, current in zip(reds, reds[1:]):
        if current - previous == 1:
            run += 1
        else:
            if run >= 2:
                groups += 1
            run = 1
    if run >= 2:
        groups += 1
    return groups


def ac_value(reds: Sequence[int]) -> int:
    """AC 值：两两差值中不同值的个数，减去 (号码个数 - 1)。"""
    differences = {abs(a - b) for index, a in enumerate(reds) for b in reds[index + 1 :]}
    return len(differences) - (len(reds) - 1)


def repeat_counts(draws: Sequence[Draw]) -> list[int]:
    """每期与上一期重复的红球个数。最新一期没有上一期，故结果比期数少 1。"""
    return [
        len(set(draws[index].reds) & set(draws[index + 1].reds))
        for index in range(len(draws) - 1)
    ]


def histogram(values: Sequence[int], low: int, high: int, width: int) -> dict:
    """把一组整数按固定宽度分桶，返回标签与计数。"""
    buckets: Counter[int] = Counter()
    for value in values:
        index = (value - low) // width
        buckets[index] += 1
    labels = []
    counts = []
    start = low
    while start <= high:
        end = min(start + width - 1, high)
        labels.append(f"{start}-{end}" if end > start else str(start))
        counts.append(buckets.get((start - low) // width, 0))
        start += width
    return {"labels": labels, "counts": counts}


def _categorical_distribution(
    values: Sequence[int], categories: Sequence[int], labeler: Callable[[int], str]
) -> dict:
    counts = Counter(values)
    return {
        "labels": [labeler(category) for category in categories],
        "counts": [counts.get(category, 0) for category in categories],
    }


def chi_square_uniformity(observed: Sequence[int]) -> dict:
    """卡方检验：号码出现次数是否与"均匀分布"假设一致。

    p 值大于 0.05 表示没有足够证据拒绝均匀假设，
    即号码分布与「完全随机」没有统计意义上的差异。
    """
    result = scipy_stats.chisquare(f_obs=list(observed))
    return {
        "statistic": float(result.statistic),
        "p_value": float(result.pvalue),
        "dof": len(observed) - 1,
    }


def rank_frequencies(frequencies: Sequence[Frequency], top: int) -> dict:
    """按出现次数排名，次数相同时号码小的排前面。"""
    ordered = sorted(frequencies, key=lambda item: (-item.count, item.number))
    return {
        "hottest": [asdict(item) for item in ordered[:top]],
        "coldest": [asdict(item) for item in ordered[-top:][::-1]],
    }


def yearly_summary(draws: Sequence[Draw]) -> list[dict]:
    """按年份汇总期数与和值均值。"""
    grouped: dict[int, list[Draw]] = defaultdict(list)
    for draw in draws:
        grouped[draw.year].append(draw)
    summary = []
    for year in sorted(grouped):
        group = grouped[year]
        sums = [sum(draw.reds) for draw in group]
        summary.append(
            {
                "year": year,
                "periods": len(group),
                "sum_mean": round(sum(sums) / len(sums), 2),
                "odd_mean": round(sum(odd_counts(group)) / len(group), 2),
            }
        )
    return summary


def yearly_heatmap(draws: Sequence[Draw]) -> dict:
    """号码 × 年份 的出现次数矩阵，用于热力图。"""
    years = sorted({draw.year for draw in draws})
    year_index = {year: index for index, year in enumerate(years)}
    matrix = [[0] * len(RED_NUMBERS) for _ in years]
    for draw in draws:
        row = matrix[year_index[draw.year]]
        for number in draw.reds:
            row[number - 1] += 1
    return {
        "years": years,
        "numbers": list(RED_NUMBERS),
        "matrix": matrix,
    }


def build_summary(draws: Sequence[Draw]) -> dict:
    """一次性算出报告需要的全部统计量，返回可直接序列化的字典。"""
    periods = len(draws)
    red_freq = red_frequencies(draws)
    blue_freq = blue_frequencies(draws)

    sums = sum_values(draws)
    spans = span_values(draws)
    odd = odd_counts(draws)
    big = big_counts(draws)
    repeats = repeat_counts(draws)
    zones = Counter(zone_counts(draw) for draw in draws)
    consecutive = Counter(consecutive_groups(draw.reds) for draw in draws)
    ac = Counter(ac_value(draw.reds) for draw in draws)

    red_expected = RED_BALL_COUNT / len(RED_NUMBERS) * periods
    blue_expected = periods / len(BLUE_NUMBERS)

    return {
        "meta": {
            "periods": periods,
            "first_issue": draws[0].issue if periods else None,
            "last_issue": draws[-1].issue if periods else None,
            "years": sorted({draw.year for draw in draws}),
            "red_expected": round(red_expected, 2),
            "blue_expected": round(blue_expected, 2),
        },
        "red_frequency": [asdict(item) for item in red_freq],
        "blue_frequency": [asdict(item) for item in blue_freq],
        "red_ranking": rank_frequencies(red_freq, top=6),
        "blue_ranking": rank_frequencies(blue_freq, top=4),
        "red_omission_current": current_omission(draws, RED_NUMBERS, _reds_of),
        "red_omission_max": max_omission(draws, RED_NUMBERS, _reds_of),
        "blue_omission_current": current_omission(draws, BLUE_NUMBERS, _blues_of),
        "blue_omission_max": max_omission(draws, BLUE_NUMBERS, _blues_of),
        "sums": {
            "values": sums,
            "hist": histogram(sums, low=(SUM_MIN // HIST_BIN) * HIST_BIN, high=SUM_MAX, width=HIST_BIN),
            "min": min(sums) if sums else None,
            "max": max(sums) if sums else None,
            "mean": round(sum(sums) / periods, 2) if periods else None,
            "theoretical_mean": RED_BALL_COUNT * (RED_BALL_MIN + RED_BALL_MAX) / 2,
        },
        "spans": {
            "values": spans,
            "hist": histogram(spans, low=0, high=32, width=4),
            "mean": round(sum(spans) / periods, 2) if periods else None,
        },
        "odd_even": _categorical_distribution(
            odd, range(0, RED_BALL_COUNT + 1), lambda k: f"{k}奇{RED_BALL_COUNT - k}偶"
        ),
        "big_small": _categorical_distribution(
            big, range(0, RED_BALL_COUNT + 1), lambda k: f"{k}大{RED_BALL_COUNT - k}小"
        ),
        "zones": {
            "labels": [f"{a}:{b}:{c}" for (a, b, c) in sorted(zones)],
            "counts": [zones[key] for key in sorted(zones)],
        },
        "consecutive": _categorical_distribution(
            [consecutive_groups(draw.reds) for draw in draws],
            range(0, 4),
            lambda k: "无连号" if k == 0 else f"{k} 组连号",
        ),
        # 重号个数取值 0~6，桶必须覆盖满，否则高频重号会被静默丢掉
        "repeats": _categorical_distribution(
            repeats, range(0, RED_BALL_COUNT + 1), lambda k: f"{k} 个重号"
        ),
        "ac_values": {
            "labels": [str(value) for value in sorted(ac)],
            "counts": [ac[value] for value in sorted(ac)],
            "mean": round(sum(ac_value(draw.reds) for draw in draws) / periods, 2) if periods else None,
        },
        "chi_square": {
            "red": chi_square_uniformity([item.count for item in red_freq]),
            "blue": chi_square_uniformity([item.count for item in blue_freq]),
        },
        "yearly": yearly_summary(draws),
        "heatmap": yearly_heatmap(draws),
    }
