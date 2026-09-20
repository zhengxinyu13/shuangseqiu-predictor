"""针对 ``data/`` 下真实开奖数据的完整性测试。

这组测试不检查"代码写得对不对"，而是检查数据本身能不能被信任：
数据是预测的地基，地基错了后面所有统计都会跟着错。

所有断言都基于数据自身的自洽性——格式、范围、唯一性、顺序，
以及「派生列」能否由红球重算出来——不依赖任何外部开奖资料，
因此不会因为外部信息变动而失效。
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

import openpyxl
import pytest

from shuangseqiu.data import (
    BLUE_BALL_MAX,
    BLUE_BALL_MIN,
    DATA_FILE_NAME,
    HEADER,
    RED_BALL_COUNT,
    RED_BALL_MAX,
    RED_BALL_MIN,
    SHEET_NAME,
)
from shuangseqiu.quality import MAX_PLAUSIBLE_DRAWS_PER_YEAR
from shuangseqiu.stats import blue_frequencies, current_omission, red_frequencies

# 数据量下限：低于这个数说明文件被截断或读取不完整。
MIN_EXPECTED_ROWS = 3500

# 期号年份的合理下限。历史首期年份不写死，只要求不早于一个宽松边界。
EARLIEST_PLAUSIBLE_YEAR = 2000

# 工作簿的完整工作表清单，锁定交付物的结构契约。
EXPECTED_SHEETS = ("开奖记录", "红球统计", "蓝球统计", "年度概况", "奖金与奖池", "数据校验")

# 双色球只在周二、周四、周日开奖（2005 年起固定为每周三期）。
DRAW_WEEKDAYS = frozenset({"周二", "周四", "周日"})
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 三区划分：一区 01-11、二区 12-22、三区 23-33
ZONE_BOUNDS = ((1, 11), (12, 22), (23, 33))

# 大号分界：01-16 为小号，17-33 为大号
BIG_NUMBER_MIN = 17

COLUMN = {name: index for index, name in enumerate(HEADER)}


def _cell(row: tuple, name: str):
    return row[COLUMN[name]]


def _reds(row: tuple) -> list[int]:
    return [int(_cell(row, f"红球{i}")) for i in range(1, RED_BALL_COUNT + 1)]


def _streak_label(reds: list[int]) -> str:
    """复刻数据生成脚本的「连号」写法，用于交叉核对这一列。"""
    runs: list[list[int]] = []
    current = [reds[0]]
    for previous, following in zip(reds, reds[1:]):
        if following - previous == 1:
            current.append(following)
        else:
            runs.append(current)
            current = [following]
    runs.append(current)
    return ";".join("-".join(f"{n:02d}" for n in run) for run in runs if len(run) >= 2)


# ------------------------------------------------------------------ 文件

def test_data_file_exists_and_not_empty(data_path):
    assert data_path.is_file()
    assert data_path.name == DATA_FILE_NAME
    assert data_path.stat().st_size > 0


def test_workbook_sheet_layout(data_path):
    workbook = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    try:
        assert tuple(workbook.sheetnames) == EXPECTED_SHEETS
    finally:
        workbook.close()


def test_header_matches_contract(header):
    assert header == HEADER


def test_history_is_long_enough(data_rows):
    assert len(data_rows) >= MIN_EXPECTED_ROWS, f"数据行数只有 {len(data_rows)}"


def test_parsed_count_matches_raw_row_count(draws, data_rows):
    assert len(draws) == len(data_rows), "解析后的记录数与原始行数不一致"


# ------------------------------------------------------------------ 期号

def test_issue_is_positive_integer(data_rows):
    for row_number, row in enumerate(data_rows, start=2):
        issue = _cell(row, "期号")
        assert isinstance(issue, int) and not isinstance(issue, bool), (
            f"第 {row_number} 行期号不是整数：{issue!r}"
        )
        assert issue > 0, f"第 {row_number} 行期号非正数：{issue}"


def test_issue_equals_year_times_1000_plus_index(data_rows):
    bad = [
        (int(_cell(row, "期号")), _cell(row, "年份"), _cell(row, "期序"))
        for row in data_rows
        if int(_cell(row, "期号")) != int(_cell(row, "年份")) * 1000 + int(_cell(row, "期序"))
    ]
    assert not bad, f"期号与「年份/期序」列不自洽，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_year_and_index_are_plausible(data_rows):
    current_year = dt.date.today().year
    for row_number, row in enumerate(data_rows, start=2):
        year, index = int(_cell(row, "年份")), int(_cell(row, "期序"))
        assert EARLIEST_PLAUSIBLE_YEAR <= year <= current_year, (
            f"第 {row_number} 行年份越界：{year}"
        )
        assert 1 <= index <= MAX_PLAUSIBLE_DRAWS_PER_YEAR, (
            f"第 {row_number} 行年内序号越界：{index}"
        )


def test_issue_is_unique(draws):
    counts = Counter(draw.issue for draw in draws)
    duplicates = sorted(issue for issue, count in counts.items() if count > 1)
    assert not duplicates, f"存在重复期号：{duplicates[:5]}"


def test_issue_is_strictly_descending(draws):
    keys = [draw.sort_key for draw in draws]
    assert keys == sorted(keys, reverse=True), "期号未按从新到旧排列"
    assert len(set(keys)) == len(keys), "存在重复的 (年份, 序号) 组合"


def test_issue_years_are_plausible(draws):
    current_year = dt.date.today().year
    years = [draw.year for draw in draws]
    assert min(years) >= EARLIEST_PLAUSIBLE_YEAR, f"出现异常早的年份：{min(years)}"
    assert max(years) <= current_year, f"出现未来的年份：{max(years)}"


# ------------------------------------------------------------------ 号码

def test_red_ball_cells_are_integers_in_range(data_rows):
    for row_number, row in enumerate(data_rows, start=2):
        for position in range(1, RED_BALL_COUNT + 1):
            value = _cell(row, f"红球{position}")
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"第 {row_number} 行红球{position} 不是整数：{value!r}"
            )
            assert RED_BALL_MIN <= value <= RED_BALL_MAX, (
                f"第 {row_number} 行红球{position} 越界：{value}"
            )


def test_red_balls_are_strictly_ascending_per_row(data_rows):
    bad = [int(_cell(row, "期号")) for row in data_rows if _reds(row) != sorted(set(_reds(row)))]
    assert not bad, f"红球未严格升序且不重复，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_blue_ball_cell_is_integer_in_range(data_rows):
    for row_number, row in enumerate(data_rows, start=2):
        value = _cell(row, "蓝球")
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"第 {row_number} 行蓝球不是整数：{value!r}"
        )
        assert BLUE_BALL_MIN <= value <= BLUE_BALL_MAX, f"第 {row_number} 行蓝球越界：{value}"


def test_red_ball_count(draws):
    for draw in draws:
        assert len(draw.reds) == RED_BALL_COUNT, f"{draw.issue} 红球数量为 {len(draw.reds)}"


def test_red_balls_within_range(draws):
    for draw in draws:
        for number in draw.reds:
            assert RED_BALL_MIN <= number <= RED_BALL_MAX, f"{draw.issue} 红球越界：{number}"


def test_red_balls_sorted_and_unique(draws):
    for draw in draws:
        assert list(draw.reds) == sorted(draw.reds), f"{draw.issue} 红球未升序排列"
        assert len(set(draw.reds)) == len(draw.reds), f"{draw.issue} 红球存在重复"


def test_blue_ball_within_range(draws):
    for draw in draws:
        assert BLUE_BALL_MIN <= draw.blue <= BLUE_BALL_MAX, f"{draw.issue} 蓝球越界：{draw.blue}"


# ------------------------------------------------------- 派生列须可由红球重算

def test_derived_sum_matches_red_total(data_rows):
    bad = [
        (int(_cell(row, "期号")), _cell(row, "和值"), sum(_reds(row)))
        for row in data_rows
        if int(_cell(row, "和值")) != sum(_reds(row))
    ]
    assert not bad, f"「和值」列与红球之和不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_derived_span_matches_red_range(data_rows):
    bad = [
        int(_cell(row, "期号"))
        for row in data_rows
        if int(_cell(row, "跨度")) != max(_reds(row)) - min(_reds(row))
    ]
    assert not bad, f"「跨度」列与红球极差不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_derived_odd_even_matches_red_parity(data_rows):
    bad = []
    for row in data_rows:
        odd = sum(1 for number in _reds(row) if number % 2 == 1)
        if str(_cell(row, "奇偶比")) != f"{odd}:{RED_BALL_COUNT - odd}":
            bad.append(int(_cell(row, "期号")))
    assert not bad, f"「奇偶比」列与红球不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_derived_big_small_uses_17_as_big_threshold(data_rows):
    """「大小比」列必须遵循 01-16 为小、17-33 为大的口径。"""
    bad = []
    for row in data_rows:
        big = sum(1 for number in _reds(row) if number >= BIG_NUMBER_MIN)
        if str(_cell(row, "大小比")) != f"{big}:{RED_BALL_COUNT - big}":
            bad.append(int(_cell(row, "期号")))
    assert not bad, f"「大小比」列与 01-16/17-33 口径不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_derived_zone_counts_match(data_rows):
    bad = []
    for row in data_rows:
        reds = _reds(row)
        expected = ":".join(
            str(sum(1 for number in reds if low <= number <= high)) for low, high in ZONE_BOUNDS
        )
        if str(_cell(row, "三区比")) != expected:
            bad.append(int(_cell(row, "期号")))
    assert not bad, f"「三区比」列与红球不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_derived_streak_matches_red_runs(data_rows):
    bad = []
    for row in data_rows:
        stored = _cell(row, "连号")
        stored = "" if stored is None else str(stored)
        if stored != _streak_label(_reds(row)):
            bad.append(int(_cell(row, "期号")))
    assert not bad, f"「连号」列与红球不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


# -------------------------------------------------------------- 开奖日规律

def test_draw_date_weekday_matches_real_calendar(data_rows):
    """「星期」列必须与开奖日期的真实星期一致。"""
    bad = []
    for row in data_rows:
        date = _cell(row, "开奖日期")
        assert isinstance(date, dt.datetime), f"期号 {_cell(row, '期号')} 的开奖日期非日期类型"
        if WEEKDAY_NAMES[date.weekday()] != _cell(row, "星期"):
            bad.append((int(_cell(row, "期号")), date.date().isoformat(), _cell(row, "星期")))
    assert not bad, f"「星期」列与真实日历不符，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_every_draw_falls_on_a_scheduled_weekday(data_rows):
    """双色球只在周二/周四/周日开奖；出现别的星期说明日期或号码被改过。"""
    offenders = Counter(
        str(_cell(row, "星期")) for row in data_rows if _cell(row, "星期") not in DRAW_WEEKDAYS
    )
    assert not offenders, f"存在非开奖日记录：{dict(offenders)}"


def test_draw_dates_are_unique_per_issue(data_rows):
    """同一开奖日期不应对应两个期号。"""
    counts = Counter(_cell(row, "开奖日期") for row in data_rows)
    duplicates = sorted(str(day) for day, count in counts.items() if count > 1)
    assert not duplicates, f"同一天出现多个期号：{duplicates[:5]}"


# ------------------------------------------------- 与统计附表交叉核对（防口径漂移）

def test_red_stats_sheet_agrees_with_package(draws, sheet_rows):
    """``红球统计`` 工作表的频次与当前遗漏，必须和包内算法算出的一致。"""
    rows = sheet_rows("红球统计")[1:]
    sheet = {str(row[0]): row for row in rows}
    frequencies = {item.number: item for item in red_frequencies(draws)}
    omission = current_omission(draws, tuple(range(1, 34)), lambda draw: draw.reds)

    assert len(sheet) == 33
    for number in range(1, 34):
        row = sheet[f"{number:02d}"]
        assert int(row[1]) == frequencies[number].count, f"红球 {number:02d} 出现次数不一致"
        assert float(row[2]) == pytest.approx(frequencies[number].rate), f"红球 {number:02d} 频率不一致"
        assert int(row[4]) == omission[number], f"红球 {number:02d} 当前遗漏不一致"


def test_blue_stats_sheet_agrees_with_package(draws, sheet_rows):
    """``蓝球统计`` 工作表的频次与当前遗漏，必须和包内算法算出的一致。"""
    rows = sheet_rows("蓝球统计")[1:]
    sheet = {str(row[0]): row for row in rows}
    frequencies = {item.number: item for item in blue_frequencies(draws)}
    omission = current_omission(draws, tuple(range(1, 17)), lambda draw: (draw.blue,))

    assert len(sheet) == 16
    for number in range(1, 17):
        row = sheet[f"{number:02d}"]
        assert int(row[1]) == frequencies[number].count, f"蓝球 {number:02d} 出现次数不一致"
        assert float(row[2]) == pytest.approx(frequencies[number].rate), f"蓝球 {number:02d} 频率不一致"
        assert int(row[4]) == omission[number], f"蓝球 {number:02d} 当前遗漏不一致"
