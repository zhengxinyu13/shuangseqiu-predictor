"""pytest 共享 fixture。

这里只负责"把数据准备好"，不放任何断言——断言全部集中在 ``test_*.py`` 里，
这样测试失败时能一眼看出是数据问题还是解析代码问题。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shuangseqiu.data import Draw, find_data_file, load_draws, load_rows


@pytest.fixture(scope="session")
def data_path() -> Path:
    """历史开奖数据文件的绝对路径。"""
    return find_data_file()


@pytest.fixture(scope="session")
def workbook_rows(data_path: Path) -> list[tuple]:
    """工作簿的全部原始行（含表头）。"""
    return load_rows(data_path)


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
