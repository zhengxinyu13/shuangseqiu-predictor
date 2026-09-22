"""整本工作簿的读取、派生字段重算与回写。

与 :mod:`shuangseqiu.data` 的分工：

- ``data.py`` 只读「开奖记录」表，供统计与报告消费，**不写文件**；
- 本模块负责整本工作簿的读取、派生字段重算与写回，供「检查更新」按钮使用。

之所以不去复用 ``data/scripts/build_dataset.py``：那个目录被 ``.gitignore`` 排除，
包内代码一旦依赖它，干净克隆下来就跑不起来。派生口径与它保持一致
（和值 / 跨度 / 奇偶比 / 大小比 / 三区比 / 连号 / 遗漏），并由测试守住两者同源。

行序约定（与既有文件一致，改动前务必确认）：

- ``开奖记录``、``奖金与奖池``：**倒序**，最新一期在最上；
- ``红球统计``、``蓝球统计``、``年度概况``：升序（统计附表）。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

SHEET_RECORDS = "开奖记录"
SHEET_RED_STATS = "红球统计"
SHEET_BLUE_STATS = "蓝球统计"
SHEET_YEARLY = "年度概况"
SHEET_BONUS = "奖金与奖池"
SHEET_CHECKS = "数据校验"

SHEET_ORDER: tuple[str, ...] = (
    SHEET_RECORDS,
    SHEET_RED_STATS,
    SHEET_BLUE_STATS,
    SHEET_YEARLY,
    SHEET_BONUS,
    SHEET_CHECKS,
)

RECORD_HEADER: tuple[str, ...] = (
    "期号", "年份", "期序", "开奖日期", "星期",
    "红球1", "红球2", "红球3", "红球4", "红球5", "红球6", "蓝球",
    "和值", "跨度", "奇偶比", "大小比", "三区比", "连号",
)
BONUS_HEADER: tuple[str, ...] = (
    "期号", "销售额", "奖池", "一等奖注数", "一等奖单注奖金", "二等奖注数", "二等奖单注奖金",
)
NUMBER_STAT_HEADER: tuple[str, ...] = (
    "红球号码", "出现次数", "出现频率", "理论频率", "当前遗漏", "最大遗漏", "平均遗漏",
)
BLUE_STAT_HEADER: tuple[str, ...] = (
    "蓝球号码", "出现次数", "出现频率", "理论频率", "当前遗漏", "最大遗漏", "平均遗漏",
)
YEARLY_HEADER: tuple[str, ...] = (
    "年份", "期数", "首期", "末期", "首期日期", "末期日期", "开奖星期", "平均每周开奖期数",
)
CHECK_HEADER: tuple[str, ...] = ("类别", "项目", "结论", "明细")

WEEKDAYS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
DRAW_WEEKDAYS: tuple[int, ...] = (1, 3, 6)  # 双色球固定周二 / 周四 / 周日开奖
BIG_THRESHOLD = 17  # 01-16 为小，17-33 为大
ZONES: tuple[tuple[int, int], ...] = ((1, 11), (12, 22), (23, 33))
RED_NUMBERS: tuple[int, ...] = tuple(range(1, 34))
BLUE_NUMBERS: tuple[int, ...] = tuple(range(1, 17))

# 原表期号是整数；openpyxl 读回来也可能是 datetime / float，统一在这里归一
_RECORD_INDEX = {name: position for position, name in enumerate(RECORD_HEADER)}
_BONUS_INDEX = {name: position for position, name in enumerate(BONUS_HEADER)}


@dataclass(frozen=True)
class DrawRecord:
    """一期开奖记录，含从「奖金与奖池」表带过来的派奖字段。

    期号用整数（``2026108``）存储，与 Excel 单元格一致；展示用的 ``2026108期``
    标签由 :attr:`label` 派生。
    """

    issue: int
    date: date
    reds: tuple[int, ...]
    blue: int
    sales: int | None = None
    pool: int | None = None
    first_winners: int | None = None
    first_prize: int | None = None
    second_winners: int | None = None
    second_prize: int | None = None

    @property
    def year(self) -> int:
        return self.issue // 1000

    @property
    def index(self) -> int:
        return self.issue % 1000

    @property
    def label(self) -> str:
        """展示用期号标签，如 ``2026108期``。"""
        return f"{self.issue}期"

    @property
    def red_sum(self) -> int:
        return sum(self.reds)

    @property
    def span(self) -> int:
        return max(self.reds) - min(self.reds)

    @property
    def has_bonus(self) -> bool:
        """是否带有销售额（拥挤指数计算的前提）。

        刻意做成属性而不是方法：写成 ``record.has_bonus`` 少打一对括号时，
        方法对象恒为真、过滤会被静默跳过；属性则会因为不可调用而立刻报错。
        """
        return bool(self.sales)


# --------------------------------------------------------------------------
# 派生字段
# --------------------------------------------------------------------------

def consecutive_runs(reds: Sequence[int]) -> list[list[int]]:
    """把红球切成连续段，返回所有段（含长度为 1 的孤立段）。

    >>> consecutive_runs((6, 11, 13, 14, 20, 28))
    [[6], [11], [13, 14], [20], [28]]
    """
    ordered = sorted(reds)
    runs: list[list[int]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if current - previous == 1:
            runs[-1].append(current)
        else:
            runs.append([current])
    return runs


def longest_run(reds: Sequence[int]) -> int:
    """最长连号长度（无连号时为 1）。

    >>> longest_run((13, 14, 15, 20, 28, 33))
    3
    """
    return max(len(run) for run in consecutive_runs(reds))


def streak_text(reds: Sequence[int]) -> str | None:
    """连号描述：连续段用 ``-`` 连接、多段用 ``;`` 分隔，无连号返回 None。

    >>> streak_text((6, 11, 13, 14, 20, 28))
    '13-14'
    >>> streak_text((1, 5, 9, 17, 24, 33)) is None
    True
    """
    text = ";".join(
        "-".join(f"{n:02d}" for n in run) for run in consecutive_runs(reds) if len(run) >= 2
    )
    return text or None


def odd_even_text(reds: Sequence[int]) -> str:
    """奇偶比，如 ``2:4``（奇数:偶数）。"""
    odd = sum(1 for n in reds if n % 2)
    return f"{odd}:{len(reds) - odd}"


def big_small_text(reds: Sequence[int]) -> str:
    """大小比，如 ``2:4``（大:小），以 :data:`BIG_THRESHOLD` 为界。"""
    big = sum(1 for n in reds if n >= BIG_THRESHOLD)
    return f"{big}:{len(reds) - big}"


def zone_text(reds: Sequence[int]) -> str:
    """三区比，如 ``2:3:1``。"""
    return ":".join(str(sum(1 for n in reds if low <= n <= high)) for low, high in ZONES)


def weekday_cn(day: date) -> str:
    """中文星期，如 ``周四``。"""
    return WEEKDAYS[day.weekday()]


def as_date(value: Any) -> date:
    """把单元格值归一成 :class:`datetime.date`。

    Raises:
        ValueError: 值既不是 datetime 也不是可解析的日期文本时。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    raise ValueError(f"无法解析成日期：{value!r}")


def as_int(value: Any) -> int | None:
    """把单元格值归一成整数，空值 / 空串返回 None。"""
    if value is None or value == "":
        return None
    return int(value)


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------

def _read_records_sheet(worksheet) -> dict[int, tuple[date, tuple[int, ...], int]]:
    """读「开奖记录」表，返回 ``期号 -> (日期, 红球, 蓝球)``。"""
    rows = worksheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError(f"工作表 {worksheet.title!r} 是空的") from error
    names = tuple("" if cell is None else str(cell) for cell in header)
    if names != RECORD_HEADER:
        raise ValueError(f"「{SHEET_RECORDS}」表头不符合预期：{names!r} != {RECORD_HEADER!r}")

    out: dict[int, tuple[date, tuple[int, ...], int]] = {}
    for row in rows:
        if row[_RECORD_INDEX["期号"]] is None:
            continue
        issue = int(row[_RECORD_INDEX["期号"]])
        reds = tuple(int(row[_RECORD_INDEX[f"红球{n}"]]) for n in range(1, 7))
        out[issue] = (as_date(row[_RECORD_INDEX["开奖日期"]]), reds, int(row[_RECORD_INDEX["蓝球"]]))
    return out


def _read_bonus_sheet(worksheet) -> dict[int, dict[str, int | None]]:
    """读「奖金与奖池」表，返回 ``期号 -> 派奖字段``。"""
    rows = worksheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError(f"工作表 {worksheet.title!r} 是空的") from error
    names = tuple("" if cell is None else str(cell) for cell in header)
    if names != BONUS_HEADER:
        raise ValueError(f"「{SHEET_BONUS}」表头不符合预期：{names!r} != {BONUS_HEADER!r}")

    fields = {
        "sales": "销售额",
        "pool": "奖池",
        "first_winners": "一等奖注数",
        "first_prize": "一等奖单注奖金",
        "second_winners": "二等奖注数",
        "second_prize": "二等奖单注奖金",
    }
    out: dict[int, dict[str, int | None]] = {}
    for row in rows:
        if row[_BONUS_INDEX["期号"]] is None:
            continue
        issue = int(row[_BONUS_INDEX["期号"]])
        out[issue] = {key: as_int(row[_BONUS_INDEX[name]]) for key, name in fields.items()}
    return out


def read_records(path: Path | str) -> list[DrawRecord]:
    """读取整本工作簿，合并「开奖记录」与「奖金与奖池」，返回**按期号升序**的记录。

    Args:
        path: 工作簿路径。

    Returns:
        升序排列的记录列表（升序是统计计算的规范顺序，倒序在写出时恢复）。

    Raises:
        ValueError: 缺少必需工作表、表头不符，或两表期号对不上时。
    """
    target = Path(path)
    workbook = openpyxl.load_workbook(target, read_only=True, data_only=True)
    try:
        for name in (SHEET_RECORDS, SHEET_BONUS):
            if name not in workbook.sheetnames:
                raise ValueError(f"工作簿缺少工作表 {name!r}，实际为 {workbook.sheetnames!r}")
        records = _read_records_sheet(workbook[SHEET_RECORDS])
        bonus = _read_bonus_sheet(workbook[SHEET_BONUS])
    finally:
        workbook.close()

    orphan = sorted(set(bonus) - set(records))
    if orphan:
        raise ValueError(f"「{SHEET_BONUS}」存在「{SHEET_RECORDS}」没有的期号：{orphan[:5]}")

    return [
        DrawRecord(
            issue=issue,
            date=records[issue][0],
            reds=records[issue][1],
            blue=records[issue][2],
            **(bonus.get(issue) or {}),
        )
        for issue in sorted(records)
    ]


# --------------------------------------------------------------------------
# 各表行数据
# --------------------------------------------------------------------------

def record_rows(records: Sequence[DrawRecord]) -> list[list[Any]]:
    """构造「开奖记录」数据行，**按期号倒序**（最新在最上）。"""
    return [
        [
            record.issue,
            record.year,
            record.index,
            record.date,
            weekday_cn(record.date),
            *record.reds,
            record.blue,
            record.red_sum,
            record.span,
            odd_even_text(record.reds),
            big_small_text(record.reds),
            zone_text(record.reds),
            streak_text(record.reds),
        ]
        for record in sorted(records, key=lambda r: r.issue, reverse=True)
    ]


def bonus_rows(records: Sequence[DrawRecord]) -> list[list[Any]]:
    """构造「奖金与奖池」数据行，**按期号倒序**。"""
    return [
        [
            record.issue,
            record.sales,
            record.pool,
            record.first_winners,
            record.first_prize,
            record.second_winners,
            record.second_prize,
        ]
        for record in sorted(records, key=lambda r: r.issue, reverse=True)
    ]


def _number_stat_rows(
    records: Sequence[DrawRecord],
    numbers: Iterable[int],
    hit_of,
    theoretical: float,
) -> list[list[Any]]:
    """号码频次与遗漏统计的公共实现。

    遗漏类指标依赖「最后出现位置」，因此入参**必须已按期号升序**，
    否则会把最久远的一次当成最近一次。

    Args:
        records: 升序记录。
        numbers: 要统计的号码集合。
        hit_of: 取某期该号码是否出现 / 命中蓝球的可调用对象。
        theoretical: 理论出现频率（红球 6/33，蓝球 1/16）。
    """
    total = len(records)
    out: list[list[Any]] = []
    for number in numbers:
        hits = [position for position, record in enumerate(records) if hit_of(record, number)]
        gaps = [later - earlier for earlier, later in zip(hits, hits[1:])]
        out.append(
            [
                f"{number:02d}",
                len(hits),
                len(hits) / total if total else 0.0,
                theoretical,
                total - 1 - hits[-1] if hits else total,
                max(gaps) - 1 if gaps else 0,
                round(sum(gap - 1 for gap in gaps) / len(gaps), 1) if gaps else 0.0,
            ]
        )
    return out


def red_stat_rows(records: Sequence[DrawRecord]) -> list[list[Any]]:
    """红球 01-33 的频次与遗漏。"""
    return _number_stat_rows(records, RED_NUMBERS, lambda r, n: n in r.reds, 6 / 33)


def blue_stat_rows(records: Sequence[DrawRecord]) -> list[list[Any]]:
    """蓝球 01-16 的频次与遗漏。"""
    return _number_stat_rows(records, BLUE_NUMBERS, lambda r, n: r.blue == n, 1 / 16)


def yearly_rows(records: Sequence[DrawRecord]) -> list[list[Any]]:
    """按年汇总期数、起止日期与开奖周期（年份升序）。"""
    grouped: dict[int, list[DrawRecord]] = {}
    for record in records:
        grouped.setdefault(record.year, []).append(record)

    out: list[list[Any]] = []
    for year in sorted(grouped):
        group = grouped[year]
        days = sorted(r.date for r in group)
        weekdays = {d.weekday() for d in days}
        rule = "、".join(name for position, name in enumerate(WEEKDAYS) if position in weekdays)
        span_weeks = (days[-1] - days[0]).days / 7 + 1
        out.append(
            [
                year,
                len(group),
                min(r.issue for r in group),
                max(r.issue for r in group),
                days[0],
                days[-1],
                rule,
                round(len(group) / span_weeks, 2),
            ]
        )
    return out


def build_checks(
    records: Sequence[DrawRecord],
    extra_rows: Sequence[Sequence[Any]] = (),
) -> list[list[Any]]:
    """对当前数据做自检，返回「数据校验」表数据行。

    Args:
        records: 升序记录。
        extra_rows: 调用方追加的行（如本次更新的来源与官方比对结论）。

    Returns:
        ``(类别, 项目, 结论, 明细)`` 四元组列表，自检项在前。
    """
    rows: list[list[Any]] = []
    total = len(records)

    def add(category: str, item: str, passed: bool, detail: str, warn_only: bool = True) -> None:
        rows.append([category, item, "通过" if passed else ("警告" if warn_only else "失败"), detail])

    duplicates = total - len({r.issue for r in records})
    add("完整性", "期号唯一性", duplicates == 0, f"重复 {duplicates} 条")
    span = f"{min(r.issue for r in records)} ~ {max(r.issue for r in records)}" if total else "（无数据）"
    add("完整性", "总期数", total > 0, f"{total} 期（{span}）")

    bad_red = sum(1 for r in records if any(not 1 <= n <= 33 for n in r.reds))
    add("完整性", "红球取值范围", bad_red == 0, f"越界 {bad_red} 条")
    bad_blue = sum(1 for r in records if not 1 <= r.blue <= 16)
    add("完整性", "蓝球取值范围", bad_blue == 0, f"越界 {bad_blue} 条")
    unsorted = sum(1 for r in records if list(r.reds) != sorted(set(r.reds)) or len(r.reds) != 6)
    add("完整性", "红球升序且不重复", unsorted == 0, f"异常 {unsorted} 条")

    gaps: list[str] = []
    for year in sorted({r.year for r in records}):
        indices = sorted(r.index for r in records if r.year == year)
        if indices != list(range(1, len(indices) + 1)):
            gaps.append(str(year))
    add("完整性", "年内期号连续性", not gaps,
        "每年自 001 起连续" if not gaps else "缺口年份: " + "、".join(gaps))

    off_schedule = sum(1 for r in records if r.date.weekday() not in DRAW_WEEKDAYS)
    add("规律合理性", "开奖日均落在周二/周四/周日", off_schedule == 0, f"异常 {off_schedule} 期")
    add("呈现", "开奖记录排序", True, "按期号倒序，最新一期在最上（统计附表仍按号码/年份升序）")

    rows.extend([list(row) for row in extra_rows])
    return rows


def build_sheets(
    records: Sequence[DrawRecord],
    checks: Sequence[Sequence[Any]],
) -> dict[str, tuple[tuple[str, ...], list[list[Any]]]]:
    """把记录组装成 6 个工作表的 ``(表头, 数据行)``。

    返回的 dict 已按 :data:`SHEET_ORDER` 排好序，且各表行序符合文件约定。
    """
    ordered = sorted(records, key=lambda r: r.issue)
    sheets: dict[str, tuple[tuple[str, ...], list[list[Any]]]] = {
        SHEET_RECORDS: (RECORD_HEADER, record_rows(ordered)),
        SHEET_RED_STATS: (NUMBER_STAT_HEADER, red_stat_rows(ordered)),
        SHEET_BLUE_STATS: (BLUE_STAT_HEADER, blue_stat_rows(ordered)),
        SHEET_YEARLY: (YEARLY_HEADER, yearly_rows(ordered)),
        SHEET_BONUS: (BONUS_HEADER, bonus_rows(ordered)),
        SHEET_CHECKS: (CHECK_HEADER, [list(row) for row in checks]),
    }
    return {name: sheets[name] for name in SHEET_ORDER}


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------

# 样式对象一律做成模块级常量。openpyxl 的样式是不可变值对象，可以被成千上万个
# 单元格安全共享；原先在双层循环里逐格 new 一个 Border，3500 行的表要多造 6 万多个
# 临时对象，这是「检查更新」慢的主因。
_HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
_RED_FILL = PatternFill("solid", fgColor="FDE7E7")
_BLUE_FILL = PatternFill("solid", fgColor="E4EEFB")
_ALT_FILL = PatternFill("solid", fgColor="F7F9FC")
_THIN_SIDE = Side(style="thin", color="D6DCE4")
_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)
_CENTERED = Alignment(horizontal="center")
_HEAD_ALIGN = Alignment(horizontal="center", vertical="center")

_RED_BALL_COLUMNS: tuple[str, ...] = tuple(f"红球{n}" for n in range(1, 7))
_CENTERED_COLUMNS: tuple[str, ...] = (
    "期号", "年份", "期序", "星期", "蓝球", "和值", "跨度", "奇偶比", "大小比", "三区比", "连号",
)

_WIDTH_SAMPLE_ROWS = 300


def _column_indexes(worksheet, names: Sequence[str]) -> list[int]:
    """把列名映射成 1 起的列号（一次读完表头，供逐行遍历时按下标取用）。"""
    positions = {cell.value: cell.column for cell in worksheet[1]}
    return [positions[name] for name in names]


def _apply_style(workbook, sheets: Mapping[str, tuple[Sequence[str], Sequence[Sequence[Any]]]]) -> None:
    """按原表样式统一美化：深蓝表头、斑马纹、红蓝球底色、日期与百分比格式。

    特殊样式只对**存在**的工作表生效，因此这里也接受只含部分表的临时工作簿。

    性能要点：整表只用 ``iter_rows()`` 走一趟，边框与斑马纹在同一次遍历里打完，
    不再对每个单元格调一次 ``worksheet.cell(row, column)``。后者是按行列号做字典
    查找，3500 行 × 18 列要调 6 万多次，实测仅这一步就占 10 秒以上。
    """
    for worksheet in workbook.worksheets:
        for cell in worksheet[1]:
            cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
            cell.alignment = _HEAD_ALIGN
        worksheet.freeze_panes = "A2"
        worksheet.row_dimensions[1].height = 22

        # 先把行取出来复用：列宽取样与样式遍历共用同一份，避免重复走访存接口
        body_rows = list(worksheet.iter_rows(min_row=2))
        sample_rows = [tuple(worksheet[1]), *body_rows[: _WIDTH_SAMPLE_ROWS - 1]]

        for column in range(1, worksheet.max_column + 1):
            width = max(
                (len(str(row[column - 1].value or "")) for row in sample_rows if len(row) >= column),
                default=8,
            )
            worksheet.column_dimensions[get_column_letter(column)].width = min(max(width * 1.8, 9), 40)

        for index, cells in enumerate(body_rows, start=2):  # 边框 + 斑马纹，一趟搞定
            striped = index % 2 == 0
            for cell in cells:
                cell.border = _CELL_BORDER
                if striped and cell.fill.patternType is None:
                    cell.fill = _ALT_FILL

    if SHEET_RECORDS in workbook.sheetnames:
        records_sheet = workbook[SHEET_RECORDS]
        red_columns = _column_indexes(records_sheet, _RED_BALL_COLUMNS)
        centered_columns = _column_indexes(records_sheet, _CENTERED_COLUMNS)
        blue_column = _column_indexes(records_sheet, ("蓝球",))[0]
        date_column = _column_indexes(records_sheet, ("开奖日期",))[0]

        for cells in records_sheet.iter_rows(min_row=2):
            for column in red_columns:
                cell = cells[column - 1]
                cell.fill, cell.alignment = _RED_FILL, _CENTERED
            for column in centered_columns:
                cells[column - 1].alignment = _CENTERED
            cells[blue_column - 1].fill = _BLUE_FILL
            date_cell = cells[date_column - 1]
            date_cell.number_format, date_cell.alignment = "yyyy-mm-dd", _CENTERED
        records_sheet.auto_filter.ref = records_sheet.dimensions

    for name in (SHEET_RED_STATS, SHEET_BLUE_STATS):
        if name not in workbook.sheetnames:
            continue
        worksheet = workbook[name]
        columns = [cell.value for cell in worksheet[1]]
        for column_name in ("出现频率", "理论频率"):
            position = columns.index(column_name)
            for cells in worksheet.iter_rows(min_row=2):
                cells[position].number_format = "0.00%"

    if SHEET_CHECKS in workbook.sheetnames:
        checks_sheet = workbook[SHEET_CHECKS]
        for index, width in enumerate((10, 26, 10, 66), start=1):
            checks_sheet.column_dimensions[get_column_letter(index)].width = width

    if SHEET_BONUS in workbook.sheetnames:
        bonus_sheet = workbook[SHEET_BONUS]
        bonus_columns = [cell.value for cell in bonus_sheet[1]]
        for column_name in ("销售额", "奖池", "一等奖单注奖金", "二等奖单注奖金"):
            position = bonus_columns.index(column_name)
            for cells in bonus_sheet.iter_rows(min_row=2):
                cells[position].number_format = "#,##0"


def write_workbook(
    path: Path | str,
    sheets: Mapping[str, tuple[Sequence[str], Sequence[Sequence[Any]]]],
) -> Path:
    """把各表写入工作簿，先写临时文件再原子替换，避免写一半留下坏文件。

    Args:
        path: 目标路径。
        sheets: ``表名 -> (表头, 数据行)``，写入顺序即字典顺序。

    Returns:
        实际写入的路径。

    Raises:
        OSError: 目标文件被占用（如被 Excel / 腾讯文档预览锁住）且无法替换时。
    """
    target = Path(path)
    temp = target.with_name(target.stem + ".writing" + target.suffix)

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, (header, rows) in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        worksheet.append(list(header))
        for row in rows:
            worksheet.append(list(row))
    _apply_style(workbook, sheets)

    workbook.save(temp)
    workbook.close()
    try:
        os.replace(temp, target)
    except OSError as error:
        if temp.exists():
            temp.unlink(missing_ok=True)
        raise OSError(
            f"无法替换 {target}：文件可能正被 Excel / 预览占用，请先关闭后重试。（{error}）"
        ) from error
    return target


def write_records(
    path: Path | str,
    records: Sequence[DrawRecord],
    checks: Sequence[Sequence[Any]],
) -> Path:
    """重算全部派生字段并回写整本工作簿。

    Returns:
        实际写入的路径。
    """
    return write_workbook(path, build_sheets(records, checks))
