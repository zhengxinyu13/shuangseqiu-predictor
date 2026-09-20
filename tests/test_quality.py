"""数据质量诊断模块的测试。

合成数据用来验证判定逻辑本身；
真实数据用来锁定这次诊断的结论 —— 一旦数据文件被替换或补全，这些断言会立刻报警。
"""

from __future__ import annotations

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


def test_real_data_record_count(draws):
    assert len(draws) == 4330
    assert build_quality_report(draws).total_records == 4330


def test_real_data_suspect_years(draws):
    report = build_quality_report(draws)

    assert report.suspect_years == (2003, 2004, 2005, 2006)
    assert report.suspect_count == 3744
    assert report.trusted_years == (2022, 2023, 2024, 2025, 2026)
    assert report.trusted_count == 586


def test_real_data_suspect_year_period_counts(draws):
    report = build_quality_report(draws)

    assert report.year_count(2003) == 999
    assert report.year_count(2004) == 1000
    assert report.year_count(2005) == 1000
    assert report.year_count(2006) == 745


def test_real_data_missing_years_are_2007_to_2021(draws):
    assert build_quality_report(draws).missing_years == tuple(range(2007, 2022))


def test_real_data_2026_gap_is_indexes_40_to_47(draws):
    report = build_quality_report(draws)
    profile = next(item for item in report.profiles if item.year == 2026)

    assert profile.missing_indexes == tuple(range(40, 48))
    assert profile.count == 100
    assert profile.starts_at_one is True
