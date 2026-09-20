"""pytest 共享 fixture。

这里只负责"把数据准备好"，不放任何断言——断言全部集中在 ``test_*.py`` 里，
这样测试失败时能一眼看出是数据问题还是解析代码问题。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from shuangseqiu.data import Draw, find_data_file, load_draws, load_rows, parse_draw
from shuangseqiu.dataset import (
    DrawRecord,
    build_checks,
    read_records,
    write_records,
)
from shuangseqiu.updater import RemoteDraw


@pytest.fixture(scope="session")
def data_path() -> Path:
    """历史开奖数据文件的绝对路径。"""
    return find_data_file()


@pytest.fixture(scope="session")
def workbook_rows(data_path: Path) -> list[tuple]:
    """``开奖记录`` 工作表的全部原始行（含表头）。"""
    return load_rows(data_path)


@pytest.fixture(scope="session")
def sheet_rows(data_path: Path):
    """按名称读取工作簿任意工作表，返回 ``(name) -> 行元组列表`` 的读取器。"""

    def _load(name: str) -> list[tuple]:
        workbook = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
        try:
            return [tuple(row) for row in workbook[name].iter_rows(values_only=True)]
        finally:
            workbook.close()

    return _load


@pytest.fixture(scope="session")
def header(workbook_rows: list[tuple]) -> tuple:
    """表头行。"""
    return workbook_rows[0]


@pytest.fixture(scope="session")
def data_rows(workbook_rows: list[tuple]) -> list[tuple]:
    """去掉表头后的数据行。"""
    return workbook_rows[1:]


@pytest.fixture(scope="session")
def draws(data_path: Path) -> list[Draw]:
    """全部解析后的开奖记录，顺序与文件一致（最新在前）。"""
    return load_draws(data_path)


@pytest.fixture
def make_draw():
    """构造一条合法开奖记录的工厂函数。

    用法：``make_draw(index, reds=(1, 2, 3, 4, 5, 6), blue=1, year=2023)``
    """

    def _make(
        index: int,
        reds: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        blue: int = 1,
        year: int = 2023,
    ) -> Draw:
        balls = " ".join(f"{number:02d}" for number in reds) + f" {blue:02d}"
        return parse_draw(f"{year}{index:03d}期", balls)

    return _make


@pytest.fixture(scope="session")
def records(data_path: Path) -> list[DrawRecord]:
    """整本工作簿解析出的记录，按期号升序。"""
    return read_records(data_path)


@pytest.fixture
def make_record():
    """构造 :class:`DrawRecord` 的工厂。

    用法：``make_record(2023001, (1, 2, 3, 4, 5, 6), 7, date=dt.date(2023, 1, 3))``
    """

    def _make(
        issue: int,
        reds: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        blue: int = 1,
        day: dt.date | None = None,
        sales: int | None = None,
        first_winners: int | None = None,
    ) -> DrawRecord:
        year, index = divmod(issue, 1000)
        return DrawRecord(
            issue=issue,
            date=day or dt.date(year, 1, 1),
            reds=tuple(sorted(reds)),
            blue=blue,
            sales=sales,
            first_winners=first_winners,
        )

    return _make


@pytest.fixture
def make_remote():
    """构造 :class:`RemoteDraw` 的工厂，签名与 ``make_record`` 一致。"""

    def _make(
        issue: int,
        reds: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        blue: int = 1,
        day: dt.date | None = None,
        **bonus,
    ) -> RemoteDraw:
        year, _ = divmod(issue, 1000)
        return RemoteDraw(
            issue=issue,
            date=day or dt.date(year, 1, 1),
            reds=tuple(sorted(reds)),
            blue=blue,
            **bonus,
        )

    return _make


@pytest.fixture
def mini_workbook(tmp_path: Path):
    """把若干条记录写成一份临时工作簿，返回该文件路径。

    用于在不碰真实数据文件的前提下测试「检查更新」的写盘链路。
    """

    def _make(records: list[DrawRecord], name: str = "mini.xlsx") -> Path:
        path = tmp_path / name
        write_records(path, records, build_checks(records))
        return path

    return _make
