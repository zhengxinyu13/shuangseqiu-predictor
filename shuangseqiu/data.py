"""双色球历史开奖数据的定位、读取与解析。

数据契约（由 ``tests/test_data.py`` 负责守住）：

- 工作簿仅含一个工作表，名称为 ``Sheet1``；
- 表头两列：``期号``、``开奖号码``；
- 期号形如 ``2026108期`` —— 4 位年份 + 3 位年内序号；
- 开奖号码形如 ``06 11 13 14 20 28 16`` —— 6 个红球 + 1 个蓝球，
  均为两位数字、以单个空格分隔；
- 行序为倒序，最新一期在最前。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DATA_FILE_NAME = "双色球历史开奖数据.xlsx"

SHEET_NAME = "Sheet1"
HEADER: tuple[str, ...] = ("期号", "开奖号码")

RED_BALL_COUNT = 6
RED_BALL_MIN = 1
RED_BALL_MAX = 33
BLUE_BALL_MIN = 1
BLUE_BALL_MAX = 16

_ISSUE_PATTERN = re.compile(r"^(\d{4})(\d{3})期$")
_BALLS_PATTERN = re.compile(r"^\d{2}(?: \d{2}){6}$")


@dataclass(frozen=True)
class Draw:
    """一期开奖记录。"""

    issue: str
    year: int
    index: int
    reds: tuple[int, ...]
    blue: int

    @property
    def sort_key(self) -> tuple[int, int]:
        """可比较的期次键，越大表示越新。"""
        return (self.year, self.index)


def parse_issue(text: str) -> tuple[int, int]:
    """把 ``2026108期`` 解析成 ``(2026, 108)``。"""
    matched = _ISSUE_PATTERN.match(str(text).strip())
    if matched is None:
        raise ValueError(f"期号格式非法：{text!r}，期望形如 '2026108期'")
    return int(matched.group(1)), int(matched.group(2))


def parse_balls(text: str) -> tuple[tuple[int, ...], int]:
    """把 ``06 11 13 14 20 28 16`` 解析成 ``((6, 11, 13, 14, 20, 28), 16)``。"""
    normalized = str(text).strip()
    if _BALLS_PATTERN.match(normalized) is None:
        raise ValueError(f"号码格式非法：{text!r}，期望 7 个两位数字、以单个空格分隔")
    numbers = [int(part) for part in normalized.split(" ")]
    return tuple(numbers[:RED_BALL_COUNT]), numbers[RED_BALL_COUNT]


def parse_draw(issue: str, balls: str) -> Draw:
    """把一行原始单元格内容解析成 :class:`Draw`。"""
    year, index = parse_issue(issue)
    reds, blue = parse_balls(balls)
    return Draw(issue=str(issue).strip(), year=year, index=index, reds=reds, blue=blue)


def find_data_file(data_dir: Path | str | None = None) -> Path:
    """定位历史开奖数据文件，不存在时抛 ``FileNotFoundError``。"""
    directory = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    path = directory / DATA_FILE_NAME
    if not path.is_file():
        raise FileNotFoundError(f"数据文件不存在：{path}")
    return path


def load_rows(path: Path | str | None = None) -> list[tuple]:
    """读取工作簿全部行（含表头），返回元组列表。"""
    target = Path(path) if path is not None else find_data_file()
    workbook = openpyxl.load_workbook(target, read_only=True, data_only=True)
    try:
        if SHEET_NAME not in workbook.sheetnames:
            raise ValueError(f"工作表 {SHEET_NAME!r} 不存在，实际为 {workbook.sheetnames!r}")
        worksheet = workbook[SHEET_NAME]
        return [tuple(row) for row in worksheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def load_draws(path: Path | str | None = None) -> list[Draw]:
    """读取并解析全部开奖记录，顺序与文件一致（最新在前）。"""
    rows = load_rows(path)
    if not rows:
        raise ValueError("数据文件为空，连表头都没有")
    header = tuple("" if cell is None else str(cell) for cell in rows[0])
    if header != HEADER:
        raise ValueError(f"表头不符合预期：{header!r} != {HEADER!r}")
    return [parse_draw(row[0], row[1]) for row in rows[1:]]
