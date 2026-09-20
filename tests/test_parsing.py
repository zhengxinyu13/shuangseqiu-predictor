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
    normalize_issue,
    parse_balls,
    parse_draw,
    parse_issue,
    parse_row,
)

# 解析器只读取「期号 / 红球1-6 / 蓝球」这 8 列，
# 其余列（日期、和值、跨度、各种比值）是给人看的，构造时给占位值即可。
# 占位值一律用非空字符串或数字——空串经 openpyxl 写回后会变成 None，
# 会让「读写往返一致」的断言产生假失败。
_PLACEHOLDER = ("2026-09-17", "周四", 21, 5, "3:3", "3:3", "2:2:2", "无")


def _row(issue: int | str, reds=(1, 2, 3, 4, 5, 6), blue=1) -> list:
    """构造一行合法（但派生列为占位值）的「开奖记录」。"""
    value = int(str(issue).replace("期", ""))
    return [value, value // 1000, value % 1000, *_PLACEHOLDER[:2], *reds, blue, *_PLACEHOLDER[2:]]


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


def test_parse_issue_accepts_bare_number():
    """数据源里的期号是纯整数，解析器必须直接吃下。"""
    assert parse_issue("2026108") == (2026, 108)
    assert parse_issue(2026108) == (2026, 108)


def test_parse_issue_keeps_index_leading_zero():
    assert parse_issue("2003001期") == (2003, 1)
    assert parse_issue(2003001) == (2003, 1)


def test_parse_issue_ignores_surrounding_whitespace():
    assert parse_issue("  2023001期\t") == (2023, 1)


@pytest.mark.parametrize(
    "malformed",
    [
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


# ------------------------------------------------------------ normalize_issue


def test_normalize_issue_from_integer():
    assert normalize_issue(2026108) == "2026108期"


def test_normalize_issue_from_text():
    assert normalize_issue("2026108") == "2026108期"
    assert normalize_issue(" 2026108 ") == "2026108期"


def test_normalize_issue_keeps_existing_suffix():
    assert normalize_issue("2026108期") == "2026108期"


def test_normalize_issue_strips_float_artifact():
    """Excel 偶尔把整数读成 ``2026108.0`` 这样的浮点文本。"""
    assert normalize_issue("2026108.0") == "2026108期"


@pytest.mark.parametrize("bad", ["", "   ", None, 2026.5, [2026108]])
def test_normalize_issue_rejects_unusable_value(bad):
    with pytest.raises(ValueError):
        normalize_issue(bad)


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


def test_parse_draw_normalizes_bare_issue_to_label():
    assert parse_draw(2026108, "06 11 13 14 20 28 16").issue == "2026108期"


def test_draw_is_immutable():
    draw = parse_draw("2026108期", "06 11 13 14 20 28 16")
    with pytest.raises(dataclasses.FrozenInstanceError):
        draw.blue = 1  # type: ignore[misc]


# ------------------------------------------------------------------ parse_row


def test_parse_row_reads_six_red_columns_and_blue():
    draw = parse_row(tuple(_row(2026108, (6, 11, 13, 14, 20, 28), 16)))

    assert draw.issue == "2026108期"
    assert draw.year == 2026
    assert draw.index == 108
    assert draw.reds == (6, 11, 13, 14, 20, 28)
    assert draw.blue == 16


def test_parse_row_pads_single_digit_values():
    assert parse_row(tuple(_row(2003001, (1, 2, 3, 4, 5, 6), 7))).reds == (1, 2, 3, 4, 5, 6)


def test_parse_row_rejects_truncated_row():
    with pytest.raises(ValueError, match="列长度不足|行长度不足"):
        parse_row((2026108, 2026, 108, "2026-09-17", "周四", 6, 11, 13))


def test_parse_row_rejects_non_numeric_ball():
    row = _row(2026108)
    row[5] = "六"
    with pytest.raises(ValueError, match="非整数值"):
        parse_row(tuple(row))


def test_parse_row_rejects_empty_issue():
    row = _row(2026108)
    row[0] = None
    with pytest.raises(ValueError):
        parse_row(tuple(row))


# ------------------------------------------------------------ find_data_file


def test_find_data_file_raises_when_directory_has_no_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_data_file(tmp_path)


# ------------------------------------------------------------------ load_rows


def test_load_rows_returns_tuples_including_header(tmp_path):
    path = _make_workbook(tmp_path / "ok.xlsx", [_row(2026108)])
    rows = load_rows(path)
    assert rows[0] == HEADER
    assert rows[1] == tuple(_row(2026108))


def test_load_rows_rejects_unexpected_sheet_name(tmp_path):
    path = _make_workbook(tmp_path / "wrong-sheet.xlsx", [_row(2026108)], sheet_name="Sheet1")
    with pytest.raises(ValueError, match="工作表"):
        load_rows(path)


# ----------------------------------------------------------------- load_draws


def test_load_draws_parses_every_record(tmp_path):
    path = _make_workbook(
        tmp_path / "two.xlsx",
        [_row(2026108, (6, 11, 13, 14, 20, 28), 16), _row(2026107, (1, 5, 9, 17, 24, 33), 4)],
    )
    draws = load_draws(path)
    assert [draw.issue for draw in draws] == ["2026108期", "2026107期"]
    assert draws[1].blue == 4


def test_load_draws_returns_empty_list_for_header_only(tmp_path):
    path = _make_workbook(tmp_path / "header-only.xlsx", [])
    assert load_draws(path) == []


def test_load_draws_rejects_wrong_header(tmp_path):
    path = _make_workbook(tmp_path / "wrong-header.xlsx", [_row(2026108)], header=("期次", "开奖结果"))
    with pytest.raises(ValueError, match="表头"):
        load_draws(path)


def test_load_draws_rejects_completely_empty_sheet(tmp_path):
    path = _make_workbook(tmp_path / "empty.xlsx", [], header=None)
    with pytest.raises(ValueError, match="为空"):
        load_draws(path)


def test_load_draws_propagates_row_errors(tmp_path):
    bad = _row(2026108)
    bad[11] = 99  # 蓝球越界不会被解析器拦下，但非整数会被拦
    bad[10] = None
    path = _make_workbook(tmp_path / "bad-row.xlsx", [bad])
    with pytest.raises(ValueError, match="非整数值"):
        load_draws(path)
