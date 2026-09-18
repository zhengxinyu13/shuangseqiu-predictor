"""针对 ``data/`` 下真实开奖数据的完整性测试。

这组测试不检查"代码写得对不对"，而是检查数据本身能不能被信任：
数据是预测的地基，地基错了后面所有统计都会跟着错。

所有断言都基于数据自身的自洽性（格式、范围、唯一性、顺序），
不依赖任何外部事实，因此不会因为外部信息变动而失效。
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import openpyxl

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

ISSUE_TEXT_PATTERN = re.compile(r"^\d{7}期$")
BALLS_TEXT_PATTERN = re.compile(r"^\d{2}(?: \d{2}){6}$")

# 数据量下限：低于这个数说明文件被截断或读取不完整。
MIN_EXPECTED_ROWS = 4000

# 期号年份的合理下限。历史首期年份不写死，只要求不早于一个宽松边界。
EARLIEST_PLAUSIBLE_YEAR = 2000


def test_data_file_exists_and_not_empty(data_path):
    assert data_path.is_file()
    assert data_path.name == DATA_FILE_NAME
    assert data_path.stat().st_size > 0


def test_workbook_contains_only_expected_sheet(data_path):
    workbook = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [SHEET_NAME]
    finally:
        workbook.close()


def test_header_matches_contract(header):
    assert header == HEADER


def test_history_is_long_enough(data_rows):
    assert len(data_rows) >= MIN_EXPECTED_ROWS, f"数据行数只有 {len(data_rows)}"


def test_every_row_has_two_non_empty_text_cells(data_rows):
    for row_number, row in enumerate(data_rows, start=2):
        issue, balls = row[0], row[1]
        assert isinstance(issue, str) and issue.strip(), f"第 {row_number} 行期号为空或非文本"
        assert isinstance(balls, str) and balls.strip(), f"第 {row_number} 行号码为空或非文本"


def test_issue_text_format(data_rows):
    bad = [row[0] for row in data_rows if not ISSUE_TEXT_PATTERN.match(row[0])]
    assert not bad, f"期号格式非法，共 {len(bad)} 条，前 5 条：{bad[:5]}"


def test_balls_text_format(data_rows):
    bad = [row[1] for row in data_rows if not BALLS_TEXT_PATTERN.match(row[1])]
    assert not bad, f"号码格式非法，共 {len(bad)} 条，前 5 条：{bad[:5]}"


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


def test_parsed_count_matches_raw_row_count(draws, data_rows):
    assert len(draws) == len(data_rows), "解析后的记录数与原始行数不一致"
