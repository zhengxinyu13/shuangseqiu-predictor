"""``shuangseqiu.dataset`` 的测试：派生字段重算与整本工作簿回写。

重点守住两件事：

1. **派生口径与原表同源** —— 用真实工作簿做一次「读进来→重算→写出去→逐格比对」，
   5 张数据表必须与原文件一模一样。这条测试一旦变红，说明新的派生逻辑
   和原始建表脚本已经不一致，报告的统计口径也跟着失效。
2. **写盘是原子的** —— 中途失败不能留下半截文件，也不能留下临时文件。
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import openpyxl
import pytest
from expected_data import LATEST_ISSUE, PERIODS, YEAR_COUNTS

from shuangseqiu import dataset

# 与真实文件逐格比对时的浮点容差（出现频率这类比值会有末位差异）
TOLERANCE = 1e-9

# 数据表：这些必须逐格一致。「数据校验」由本次校验重新生成，不参与比对。
DATA_SHEETS = ("开奖记录", "红球统计", "蓝球统计", "年度概况", "奖金与奖池")


# --------------------------------------------------------------------------
# 派生字段（纯函数）
# --------------------------------------------------------------------------

def test_consecutive_runs_splits_numbers_into_runs() -> None:
    assert dataset.consecutive_runs((6, 11, 13, 14, 20, 28)) == [[6], [11], [13, 14], [20], [28]]
    assert dataset.consecutive_runs((1, 2, 3, 4, 5, 6)) == [[1, 2, 3, 4, 5, 6]]


@pytest.mark.parametrize(
    ("reds", "expected"),
    [
        ((1, 5, 9, 17, 24, 33), 1),
        ((6, 11, 13, 14, 20, 28), 2),
        ((13, 14, 15, 20, 28, 33), 3),
        ((1, 2, 3, 4, 5, 6), 6),
    ],
)
def test_longest_run_counts_the_longest_streak(reds: tuple, expected: int) -> None:
    assert dataset.longest_run(reds) == expected


def test_streak_text_describes_every_streak() -> None:
    assert dataset.streak_text((6, 11, 13, 14, 20, 28)) == "13-14"
    assert dataset.streak_text((1, 2, 3, 20, 28, 33)) == "01-02-03"
    assert dataset.streak_text((1, 2, 13, 14, 20, 28)) == "01-02;13-14"
    assert dataset.streak_text((1, 5, 9, 17, 24, 33)) is None


def test_ratio_texts_match_the_documented_bins() -> None:
    # 大小号以 17 为界，三区 01-11 / 12-22 / 23-33
    reds = (6, 11, 13, 14, 20, 28)
    assert dataset.odd_even_text(reds) == "2:4"
    assert dataset.big_small_text(reds) == "2:4"
    assert dataset.zone_text(reds) == "2:3:1"
    assert dataset.zone_text((1, 11, 12, 22, 23, 33)) == "2:2:2"
    assert dataset.big_small_text((17, 18, 19, 20, 21, 22)) == "6:0"


def test_weekday_cn_is_chinese() -> None:
    assert dataset.weekday_cn(dt.date(2026, 9, 17)) == "周四"
    assert dataset.weekday_cn(dt.date(2026, 9, 15)) == "周二"


def test_as_date_accepts_excel_flavours() -> None:
    assert dataset.as_date(dt.date(2026, 9, 17)) == dt.date(2026, 9, 17)
    assert dataset.as_date(dt.datetime(2026, 9, 17, 0, 0)) == dt.date(2026, 9, 17)
    assert dataset.as_date("2026-09-17") == dt.date(2026, 9, 17)
    with pytest.raises(ValueError):
        dataset.as_date(object())


# --------------------------------------------------------------------------
# 读取真实工作簿
# --------------------------------------------------------------------------

def test_read_records_is_ascending_and_complete(records) -> None:
    assert len(records) == PERIODS
    issues = [record.issue for record in records]
    assert issues == sorted(issues), "read_records 应返回升序"
    assert records[0].issue == 2003001
    assert records[-1].issue == LATEST_ISSUE
    assert all(len(record.reds) == 6 for record in records)
    assert all(record.date.weekday() in (1, 3, 6) for record in records)


def test_read_records_merges_bonus_columns(records) -> None:
    latest = records[-1]
    assert latest.issue == LATEST_ISSUE
    assert latest.reds == (9, 12, 15, 26, 30, 33)
    assert latest.blue == 6
    assert latest.sales == 359008756
    assert latest.pool == 936353465
    assert latest.first_winners == 5
    assert latest.first_prize == 8207986
    assert latest.second_winners == 114
    assert latest.second_prize == 175876


def test_has_bonus_is_a_property_not_a_method(records) -> None:
    """哨兵：``has_bonus`` 必须能在不加括号的情况下当布尔值用。

    曾经它是个方法，``crowding`` 里漏写括号导致「销售额过滤」被静默跳过
    （方法对象恒为真），可用期数被算成了全部期数。
    改成属性后，少写括号会立刻 ``TypeError``，不会再静默出错。
    """
    record = records[-1]
    assert isinstance(record.has_bonus, bool)
    assert record.has_bonus is True
    empty = records[0]
    assert isinstance(empty.has_bonus, bool)
    assert empty.has_bonus is False  # 2003 年全部 89 期销售额为 0


def test_read_records_rejects_a_workbook_without_required_sheets(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "随便什么表"
    workbook.save(path)
    workbook.close()
    with pytest.raises(ValueError, match="缺少工作表"):
        dataset.read_records(path)


def test_read_records_rejects_a_mismatched_header(tmp_path: Path) -> None:
    path = tmp_path / "bad_header.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = dataset.SHEET_RECORDS
    sheet.append(["期号", "开奖日期"])  # 只有两列
    workbook.create_sheet(title=dataset.SHEET_BONUS).append(list(dataset.BONUS_HEADER))
    workbook.save(path)
    workbook.close()
    with pytest.raises(ValueError, match="表头不符合预期"):
        dataset.read_records(path)


def test_read_records_rejects_bonus_issues_missing_from_records(tmp_path: Path) -> None:
    path = tmp_path / "orphan.xlsx"
    workbook = openpyxl.Workbook()
    records = workbook.active
    records.title = dataset.SHEET_RECORDS
    records.append(list(dataset.RECORD_HEADER))
    records.append([2023001, 2023, 1, dt.date(2023, 1, 3), "周二", 1, 2, 3, 4, 5, 6, 7,
                    21, 5, "3:3", "0:6", "3:3:0", None])
    bonus = workbook.create_sheet(title=dataset.SHEET_BONUS)
    bonus.append(list(dataset.BONUS_HEADER))
    bonus.append([2099999, 1, 2, 3, 4, 5, 6])
    workbook.save(path)
    workbook.close()
    with pytest.raises(ValueError, match="没有的期号"):
        dataset.read_records(path)


# --------------------------------------------------------------------------
# 派生表结构与内容
# --------------------------------------------------------------------------

def test_build_sheets_keeps_the_documented_layout(records) -> None:
    sheets = dataset.build_sheets(records, dataset.build_checks(records))
    assert tuple(sheets) == dataset.SHEET_ORDER
    assert sheets[dataset.SHEET_RECORDS][0] == dataset.RECORD_HEADER
    assert sheets[dataset.SHEET_BONUS][0] == dataset.BONUS_HEADER
    assert sheets[dataset.SHEET_RED_STATS][0] == dataset.NUMBER_STAT_HEADER
    assert sheets[dataset.SHEET_CHECKS][0] == dataset.CHECK_HEADER
    assert len(sheets[dataset.SHEET_RED_STATS][1]) == 33
    assert len(sheets[dataset.SHEET_BLUE_STATS][1]) == 16
    assert len(sheets[dataset.SHEET_YEARLY][1]) == 24


def test_record_and_bonus_rows_are_reverse_chronological(records) -> None:
    sheets = dataset.build_sheets(records, dataset.build_checks(records))
    for name in (dataset.SHEET_RECORDS, dataset.SHEET_BONUS):
        issues = [row[0] for row in sheets[name][1]]
        assert issues == sorted(issues, reverse=True), f"{name} 应为倒序"
        assert issues[0] == LATEST_ISSUE
    # 统计附表仍按升序
    for number, row in enumerate(sheets[dataset.SHEET_RED_STATS][1], start=1):
        assert row[0] == f"{number:02d}"
    years = [row[0] for row in sheets[dataset.SHEET_YEARLY][1]]
    assert years == sorted(years)


def test_red_stats_recompute_matches_the_file(records, sheet_rows) -> None:
    """统计附表由代码重算的结果，必须与文件里已有的数字一致。"""
    recomputed = dataset.red_stat_rows(records)
    stored = sheet_rows(dataset.SHEET_RED_STATS)[1:]
    assert len(recomputed) == len(stored)
    for new, old in zip(recomputed, stored):
        assert new[0] == old[0]
        assert new[1] == old[1], f"{new[0]} 出现次数不一致"
        assert abs(new[2] - old[2]) < TOLERANCE
        assert new[4] == old[4], f"{new[0]} 当前遗漏不一致"
        assert new[5] == old[5], f"{new[0]} 最大遗漏不一致"
        assert abs(new[6] - old[6]) < TOLERANCE


def test_blue_stats_recompute_matches_the_file(records, sheet_rows) -> None:
    recomputed = dataset.blue_stat_rows(records)
    stored = sheet_rows(dataset.SHEET_BLUE_STATS)[1:]
    assert len(recomputed) == len(stored)
    for new, old in zip(recomputed, stored):
        assert new[1] == old[1], f"蓝球 {new[0]} 出现次数不一致"
        assert abs(new[2] - old[2]) < TOLERANCE
        assert new[4] == old[4], f"蓝球 {new[0]} 当前遗漏不一致"


def test_omission_counts_draws_back_to_the_latest_appearance() -> None:
    """遗漏必须「按升序」计算：倒序会把最久远的一次当成最近一次。"""
    day = dt.date(2023, 1, 1)
    as_list = [
        dataset.DrawRecord(issue=2023001, date=day, reds=(1, 2, 3, 4, 5, 6), blue=1),
        dataset.DrawRecord(issue=2023002, date=day, reds=(7, 8, 9, 10, 11, 12), blue=1),
        dataset.DrawRecord(issue=2023003, date=day, reds=(13, 14, 15, 16, 17, 18), blue=2),
    ]
    rows = {row[0]: row for row in dataset.red_stat_rows(as_list)}
    assert rows["01"][4] == 2  # 第 1 期出现过后再没出现
    assert rows["18"][4] == 0  # 最后一期刚出现
    assert rows["19"][4] == 3  # 从未出现 → 等于总期数


def test_yearly_rows_aggregate_per_year(records) -> None:
    rows = {row[0]: row for row in dataset.yearly_rows(records)}
    assert rows[2003][1] == 89
    assert rows[2023][1] == 151
    assert rows[2003][6] == "周四、周日"  # 2003 年只有周四、周日开奖
    assert rows[2026][1] == YEAR_COUNTS[2026]


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------

def test_build_checks_passes_on_the_real_dataset(records) -> None:
    rows = dataset.build_checks(records)
    verdicts = {(row[0], row[1]): row[2] for row in rows}
    assert all(verdict == "通过" for verdict in verdicts.values()), verdicts
    assert list(rows[0][:3]) == ["完整性", "期号唯一性", "通过"]
    assert any(row[1] == "总期数" and f"{PERIODS} 期" in row[3] for row in rows)


def test_build_checks_flags_a_non_draw_weekday(make_record) -> None:
    # 2023-01-01 是周日（合法），2023-01-02 是周一（不合法）
    monday = [dataset.DrawRecord(issue=2023001, date=dt.date(2023, 1, 2), reds=(1, 2, 3, 4, 5, 6), blue=1)]
    rows = dataset.build_checks(monday)
    row = next(row for row in rows if row[1].startswith("开奖日"))
    assert row[2] == "警告" and "异常 1 期" in row[3]


def test_build_checks_flags_a_gap_in_issue_numbers() -> None:
    day = dt.date(2023, 1, 3)
    gapped = [
        dataset.DrawRecord(issue=2023001, date=day, reds=(1, 2, 3, 4, 5, 6), blue=1),
        dataset.DrawRecord(issue=2023003, date=day, reds=(7, 8, 9, 10, 11, 12), blue=1),
    ]
    rows = dataset.build_checks(gapped)
    row = next(row for row in rows if row[1] == "年内期号连续性")
    assert row[2] == "警告" and "2023" in row[3]


def test_build_checks_appends_caller_rows(records) -> None:
    rows = dataset.build_checks(records, extra_rows=[("更新", "自定义", "通过", "备注")])
    assert rows[-1] == ["更新", "自定义", "通过", "备注"]


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------

def test_round_trip_reproduces_the_whole_workbook(records, data_path, tmp_path: Path) -> None:
    """核心回归：读进来重算再写出去，5 张数据表必须与原文件逐格一致。"""
    out = dataset.write_records(
        tmp_path / "roundtrip.xlsx", records, dataset.build_checks(records)
    )

    original = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    rebuilt = openpyxl.load_workbook(out, read_only=True, data_only=True)
    try:
        assert tuple(rebuilt.sheetnames) == dataset.SHEET_ORDER
        for name in DATA_SHEETS:
            old_rows = list(original[name].iter_rows(values_only=True))
            new_rows = list(rebuilt[name].iter_rows(values_only=True))
            assert len(old_rows) == len(new_rows), f"{name} 行数不一致"
            for index, (old, new) in enumerate(zip(old_rows, new_rows)):
                assert len(old) == len(new), f"{name} 第 {index + 1} 行列数不一致"
                for column, (left, right) in enumerate(zip(old, new)):
                    if isinstance(left, float) or isinstance(right, float):
                        assert abs(float(left or 0) - float(right or 0)) < TOLERANCE, (
                            f"{name} 第 {index + 1} 行第 {column + 1} 列：{left} != {right}"
                        )
                    else:
                        assert (left or None) == (right or None), (
                            f"{name} 第 {index + 1} 行第 {column + 1} 列：{left!r} != {right!r}"
                        )
    finally:
        original.close()
        rebuilt.close()


def test_write_workbook_leaves_no_temporary_file(records, tmp_path: Path) -> None:
    target = tmp_path / "no_temp.xlsx"
    dataset.write_records(target, records, dataset.build_checks(records))
    leftovers = [path.name for path in tmp_path.iterdir() if path.name != "no_temp.xlsx"]
    assert leftovers == [], f"留下了临时文件：{leftovers}"


def test_write_workbook_reports_a_locked_target_and_cleans_up(tmp_path: Path) -> None:
    """目标不可替换时要报出人话，并且不留下临时文件。"""
    target = tmp_path / "occupied.xlsx"
    target.mkdir()  # 用目录占住路径，os.replace 必然失败
    sheets = {
        dataset.SHEET_CHECKS: (dataset.CHECK_HEADER, [["自检", "占位", "通过", "无"]]),
    }
    with pytest.raises(OSError, match="无法替换"):
        dataset.write_workbook(target, sheets)
    leftovers = [path.name for path in tmp_path.iterdir() if path.name != "occupied.xlsx"]
    assert leftovers == [], f"失败后留下了临时文件：{leftovers}"


def test_written_dates_keep_the_excel_date_format(records, tmp_path: Path) -> None:
    out = dataset.write_records(
        tmp_path / "fmt.xlsx", records, dataset.build_checks(records)
    )
    workbook = openpyxl.load_workbook(out)
    try:
        sheet = workbook[dataset.SHEET_RECORDS]
        positions = {cell.value: cell.column for cell in sheet[1]}
        assert sheet.cell(2, positions["开奖日期"]).number_format == "yyyy-mm-dd"
        stats = workbook[dataset.SHEET_RED_STATS]
        stat_positions = {cell.value: cell.column for cell in stats[1]}
        assert stats.cell(2, stat_positions["出现频率"]).number_format == "0.00%"
        bonus = workbook[dataset.SHEET_BONUS]
        bonus_positions = {cell.value: cell.column for cell in bonus[1]}
        assert bonus.cell(2, bonus_positions["销售额"]).number_format == "#,##0"
    finally:
        workbook.close()


# --------------------------------------------------------------------------
# 样式（美化改成单趟遍历后，这些都得原样保留）
# --------------------------------------------------------------------------

def test_written_sheet_keeps_header_ball_and_zebra_styling(records, tmp_path: Path) -> None:
    """表头底色、红蓝球底色、边框、斑马纹一个都不能少。

    ``_apply_style`` 为了性能从「逐格 worksheet.cell」改成「整行 iter_rows」，
    这条测试就是那次改写的守门人：只快不改样。
    """
    subset = records[-6:]
    out = dataset.write_records(tmp_path / "style.xlsx", subset, dataset.build_checks(subset))
    workbook = openpyxl.load_workbook(out)
    try:
        sheet = workbook[dataset.SHEET_RECORDS]
        positions = {cell.value: cell.column for cell in sheet[1]}

        assert sheet.cell(1, 1).fill.start_color.rgb.endswith("1F4E79"), "表头底色丢了"
        assert sheet.cell(2, positions["红球1"]).fill.start_color.rgb.endswith("FDE7E7"), "红球底色丢了"
        assert sheet.cell(2, positions["蓝球"]).fill.start_color.rgb.endswith("E4EEFB"), "蓝球底色丢了"
        assert sheet.cell(2, 1).border.left.style == "thin", "边框丢了"
        assert sheet.freeze_panes == "A2", "冻结首行丢了"

        # 斑马纹只看没有专属底色的列（红球/蓝球列本来就恒有底色）
        striped = positions["和值"]
        assert sheet.cell(2, striped).fill.start_color.rgb.endswith("F7F9FC"), "偶数行没有斑马纹"
        assert sheet.cell(3, striped).fill.patternType is None, "奇数行不该有斑马纹"
    finally:
        workbook.close()


def test_styling_a_full_size_sheet_stays_fast(tmp_path: Path) -> None:
    """3500 行的表必须很快美化完。

    旧写法对每个单元格调一次 ``worksheet.cell(row, column)``（按行列号做字典查找），
    还在内层循环里逐格 new 一个 ``Border``：3500 行 × 18 列要跑 6 万多次，
    实测单这一步 10.7 秒，占了「检查更新」九成耗时。这里给一个宽到不会误报、
    但足以拦住退回旧写法的上限。
    """
    rows = [
        [2023000 + index, 2023, index, dt.date(2023, 1, 1), "周日",
         1, 2, 3, 4, 5, 6, 7, 28, 5, "3:3", "2:4", "2:2:2", None]
        for index in range(1, 3501)
    ]
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = dataset.SHEET_RECORDS
    worksheet.append(list(dataset.RECORD_HEADER))
    for row in rows:
        worksheet.append(row)

    started = time.perf_counter()
    dataset._apply_style(workbook, {dataset.SHEET_RECORDS: (dataset.RECORD_HEADER, rows)})
    elapsed = time.perf_counter() - started
    workbook.close()

    assert elapsed < 5.0, f"美化 3500 行花了 {elapsed:.1f} 秒，疑似退回了逐格 worksheet.cell 的写法"
