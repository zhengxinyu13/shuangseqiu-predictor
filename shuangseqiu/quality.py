"""数据质量诊断：判定哪几年的记录不可信，并切出可信分析段。

判定依据全部来自数据自身的结构，不依赖任何外部开奖资料，因此结论可复现。

核心判据 —— 年内期数上限：

双色球每周固定开奖 3 次（周二、周四、周日）。一年有 365 或 366 天，
某个星期几在一年内最多出现 53 次，故一年最多开出 ``53 x 3 = 159`` 期。
这里再放宽 1 期、取 160 作为判定阈值，宁可漏判也不误伤真实数据
（真实年份的期数约在 150 ~ 157 之间）。

因此：某年份记录数超过 160 条，则该年份在数学上不可能成立。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from shuangseqiu.data import Draw

MAX_PLAUSIBLE_DRAWS_PER_YEAR = 160
FIRST_INDEX = 1


@dataclass(frozen=True)
class YearProfile:
    """某一年记录的结构画像。"""

    year: int
    count: int
    min_index: int
    max_index: int
    missing_indexes: tuple[int, ...]

    @property
    def is_count_plausible(self) -> bool:
        """年内期数是否落在物理可能范围内。"""
        return self.count <= MAX_PLAUSIBLE_DRAWS_PER_YEAR

    @property
    def starts_at_one(self) -> bool:
        """年内序号是否从 1 开始编号。"""
        return self.min_index == FIRST_INDEX

    @property
    def is_complete(self) -> bool:
        """年内序号是否连续无缺口，且从 1 开始。"""
        return self.starts_at_one and not self.missing_indexes


@dataclass(frozen=True)
class QualityReport:
    """整份数据的质量诊断结果。"""

    total_records: int
    profiles: tuple[YearProfile, ...]
    suspect_years: tuple[int, ...]
    missing_years: tuple[int, ...]
    trusted_draws: tuple[Draw, ...]
    suspect_draws: tuple[Draw, ...]

    @property
    def trusted_count(self) -> int:
        return len(self.trusted_draws)

    @property
    def suspect_count(self) -> int:
        return len(self.suspect_draws)

    @property
    def trusted_years(self) -> tuple[int, ...]:
        return tuple(sorted({draw.year for draw in self.trusted_draws}))

    def year_count(self, year: int) -> int:
        for profile in self.profiles:
            if profile.year == year:
                return profile.count
        return 0


def profile_years(draws: Sequence[Draw]) -> tuple[YearProfile, ...]:
    """按年份汇总记录数、序号范围与缺失序号，结果按年份升序。"""
    grouped: dict[int, list[int]] = defaultdict(list)
    for draw in draws:
        grouped[draw.year].append(draw.index)

    profiles = []
    for year in sorted(grouped):
        indexes = sorted(grouped[year])
        present = set(indexes)
        missing = tuple(
            number for number in range(FIRST_INDEX, indexes[-1] + 1) if number not in present
        )
        profiles.append(
            YearProfile(
                year=year,
                count=len(indexes),
                min_index=indexes[0],
                max_index=indexes[-1],
                missing_indexes=missing,
            )
        )
    return tuple(profiles)


def build_quality_report(draws: Sequence[Draw]) -> QualityReport:
    """生成完整的数据质量诊断报告。

    只有「年内期数物理上不可能」的年份会被剔除出可信段；
    起始序号不为 1、年内存在缺口、年份断档等问题只做记录，不参与剔除 ——
    这些情形通常意味着"收集不全"而非"数据伪造"，其号码本身仍然可用。
    """
    all_draws = tuple(draws)
    profiles = profile_years(all_draws)

    suspect_years = tuple(p.year for p in profiles if not p.is_count_plausible)
    suspect_set = set(suspect_years)

    trusted = tuple(draw for draw in all_draws if draw.year not in suspect_set)
    suspect = tuple(draw for draw in all_draws if draw.year in suspect_set)

    known_years = {p.year for p in profiles}
    if profiles:
        missing_years = tuple(
            year
            for year in range(profiles[0].year, profiles[-1].year + 1)
            if year not in known_years
        )
    else:
        missing_years = ()

    return QualityReport(
        total_records=len(all_draws),
        profiles=profiles,
        suspect_years=suspect_years,
        missing_years=missing_years,
        trusted_draws=trusted,
        suspect_draws=suspect,
    )
