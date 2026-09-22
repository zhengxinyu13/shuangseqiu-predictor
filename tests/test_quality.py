"""数据质量诊断模块的测试。

合成数据用来验证判定逻辑本身；
真实数据用来锁定这次诊断的结论 —— 一旦数据文件被替换或补全，这些断言会立刻报警。
"""

from __future__ import annotations

from expected_data import PERIODS, YEAR_COUNTS, YEARS

from shuangseqiu.quality import (
    MAX_PLAUSIBLE_DRAWS_PER_YEAR,
    build_quality_report,
    profile_years,
)


def test_profile_years_reports_gap(make_draw):
    draws = [make_draw(1), make_draw(2), make_draw(4)]
    profiles = profile_years(draws)

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.year == 2023
    assert profile.count == 3
    assert profile.min_index == 1
    assert profile.max_index == 4
    assert profile.missing_indexes == (3,)
    assert profile.is_complete is False


def test_profile_years_sorted_ascending(make_draw):
    draws = [make_draw(1, year=2025), make_draw(1, year=2023), make_draw(1, year=2024)]
    assert [profile.year for profile in profile_years(draws)] == [2023, 2024, 2025]


def test_count_threshold_boundary(make_draw):
    at_limit = [make_draw(index, year=2003) for index in range(1, MAX_PLAUSIBLE_DRAWS_PER_YEAR + 1)]
    over_limit = at_limit + [make_draw(MAX_PLAUSIBLE_DRAWS_PER_YEAR + 1, year=2003)]

    assert profile_years(at_limit)[0].is_count_plausible is True
    assert profile_years(over_limit)[0].is_count_plausible is False


def test_zero_index_year_is_incomplete_but_kept(make_draw):
    draws = [make_draw(0, year=2004), make_draw(1, year=2004), make_draw(2, year=2004)]
    report = build_quality_report(draws)
    profile = report.profiles[0]

    assert profile.starts_at_one is False
    assert profile.is_complete is False
    # 起始序号不是 1 只记录、不剔除：这类问题通常意味着收集不全，而非数据伪造
    assert report.trusted_count == 3


def test_suspect_year_is_excluded_from_trusted(make_draw):
    draws = [make_draw(index, year=2003) for index in range(1, 162)]
    draws += [make_draw(1, year=2023)]
    report = build_quality_report(draws)

    assert report.suspect_years == (2003,)
    assert report.trusted_years == (2023,)
    assert report.suspect_count == 161
    assert report.trusted_count == 1


def test_missing_years_detected(make_draw):
    draws = [make_draw(1, year=2023), make_draw(1, year=2025)]
    assert build_quality_report(draws).missing_years == (2024,)


# ------------------------------------------------------------ 真实数据断言
# 期数、年度分布等随开奖变化的基线统一放在 expected_data.py，刷新方法见那个文件。


def test_real_data_record_count(draws):
    assert len(draws) == PERIODS
    assert build_quality_report(draws).total_records == PERIODS


def test_real_data_has_no_suspect_years(draws):
    """全量数据集每年期数都落在物理上限内，不存在需要剔除的年份。"""
    report = build_quality_report(draws)

    assert report.suspect_years == ()
    assert report.suspect_count == 0
    assert report.trusted_years == YEARS
    assert report.trusted_count == PERIODS


def test_real_data_has_no_missing_years(draws):
    assert build_quality_report(draws).missing_years == ()


def test_real_data_every_year_is_complete(draws):
    """每年都自 001 起编号、年内序号连续，一个缺口都没有。"""
    for profile in build_quality_report(draws).profiles:
        assert profile.starts_at_one, f"{profile.year} 年未从 001 起编号"
        assert profile.missing_indexes == (), f"{profile.year} 年缺号：{profile.missing_indexes}"
        assert profile.is_complete, f"{profile.year} 年不完整"


def test_real_data_year_period_counts_match_baseline(draws):
    """各年份期数锁定为实测基线。"""
    actual = {profile.year: profile.count for profile in build_quality_report(draws).profiles}

    assert actual == YEAR_COUNTS


def test_real_data_no_year_exceeds_physical_limit(draws):
    report = build_quality_report(draws)
    peak = max(profile.count for profile in report.profiles)

    assert peak <= MAX_PLAUSIBLE_DRAWS_PER_YEAR
    assert peak == 154  # 实测峰值
