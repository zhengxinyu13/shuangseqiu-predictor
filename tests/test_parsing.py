"""解析函数与读取函数的单元测试。

这一组用临时构造的工作簿（``tmp_path``）来测边界与异常分支，
和 ``test_data.py`` 里针对真实数据的测试互不依赖。
"""

from __future__ import annotations

import dataclasses

import openpyxl
import pytest

from shuangseqiu.data import (
    HEADER,
    SHEET_NAME,
    Draw,
    find_data_file,
    load_draws,
    load_rows,
    parse_balls,
    parse_draw,
    parse_issue,
)


def _make_workbook(path, rows, sheet_name=SHEET_NAME, header=HEADER):
    """生成一个用于测试的最小工作簿。"""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    if header is not None:
        worksheet.append(list(header))
    for row in rows:
        worksheet.append(list(row))
    workbook.save(path)
    return path


# ---------------------------------------------------------------- parse_issue


def test_parse_issue_splits_year_and_index():
    assert parse_issue("2026108期") == (2026, 108)


def test_parse_issue_keeps_index_leading_zero():
    assert parse_issue("2003001期") == (2003, 1)


def test_parse_issue_ignores_surrounding_whitespace():
    assert parse_issue("  2023001期\t") == (2023, 1)


@pytest.mark.parametrize(
    "malformed",
    [
        "2026108",  # 缺少"期"字
        "2026108 期",  # 中间夹空格
        "20261期",  # 序号只有 1 位
        "20261088期",  # 序号 4 位
        "2026年108期",  # 年份后多了字
        "abcdefg期",
        "",
    ],
)
def test_parse_issue_rejects_malformed_text(malformed):
    with pytest.raises(ValueError):
        parse_issue(malformed)


# ---------------------------------------------------------------- parse_balls


def test_parse_balls_splits_reds_and_blue():
    assert parse_balls("06 11 13 14 20 28 16") == ((6, 11, 13, 14, 20, 28), 16)


def test_parse_balls_keeps_single_digit_values_as_numbers():
    assert parse_balls("01 02 03 04 05 06 07") == ((1, 2, 3, 4, 5, 6), 7)


@pytest.mark.parametrize(
    "malformed",
    [
        "06 11 13 14 20 28",  # 只有 6 个
        "06 11 13 14 20 28 16 01",  # 8 个
        "6 11 13 14 20 28 16",  # 首个号码缺前导零
        "06  11 13 14 20 28 16",  # 双空格
        "06-11-13-14-20-28-16",  # 分隔符不对
        "06 11 13 14 20 28 1六",  # 混入非数字
        "",
    ],
)
def test_parse_balls_rejects_malformed_text(malformed):
    with pytest.raises(ValueError):
        parse_balls(malformed)


# ----------------------------------------------------------------- parse_draw


def test_parse_draw_builds_frozen_record():
    draw = parse_draw("2026108期", "06 11 13 14 20 28 16")
    assert draw == Draw(
        issue="2026108期",
        year=2026,
        index=108,
        reds=(6, 11, 13, 14, 20, 28),
        blue=16,
    )
    assert draw.sort_key == (2026, 108)


def test_draw_is_immutable():
    draw = parse_draw("2026108期", "06 11 13 14 20 28 16")
    with pytest.raises(dataclasses.FrozenInstanceError):
        draw.blue = 1  # type: ignore[misc]


# ------------------------------------------------------------ find_data_file


def test_find_data_file_raises_when_directory_has_no_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_data_file(tmp_path)


# ------------------------------------------------------------------ load_rows


def test_load_rows_returns_tuples_including_header(tmp_path):
    path = _make_workbook(tmp_path / "ok.xlsx", [["2026108期", "06 11 13 14 20 28 16"]])
    rows = load_rows(path)
    assert rows[0] == HEADER
    assert rows[1] == ("2026108期", "06 11 13 14 20 28 16")


def test_load_rows_rejects_unexpected_sheet_name(tmp_path):
    path = _make_workbook(
        tmp_path / "wrong-sheet.xlsx",
        [["2026108期", "06 11 13 14 20 28 16"]],
        sheet_name="开奖记录",
    )
    with pytest.raises(ValueError, match="工作表"):
        load_rows(path)


# ----------------------------------------------------------------- load_draws


def test_load_draws_parses_every_record(tmp_path):
    path = _make_workbook(
        tmp_path / "two.xlsx",
        [
            ["2026108期", "06 11 13 14 20 28 16"],
            ["2026107期", "01 05 09 17 24 33 04"],
        ],
    )
    draws = load_draws(path)
    assert [draw.issue for draw in draws] == ["2026108期", "2026107期"]
    assert draws[1].blue == 4


def test_load_draws_returns_empty_list_for_header_only(tmp_path):
    path = _make_workbook(tmp_path / "header-only.xlsx", [])
    assert load_draws(path) == []


def test_load_draws_rejects_wrong_header(tmp_path):
    path = _make_workbook(
        tmp_path / "wrong-header.xlsx",
        [["2026108期", "06 11 13 14 20 28 16"]],
        header=("期次", "开奖结果"),
    )
    with pytest.raises(ValueError, match="表头"):
        load_draws(path)


def test_load_draws_rejects_completely_empty_sheet(tmp_path):
    path = _make_workbook(tmp_path / "empty.xlsx", [], header=None)
    with pytest.raises(ValueError, match="为空"):
        load_draws(path)
