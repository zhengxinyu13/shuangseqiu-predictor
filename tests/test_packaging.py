"""打包分发相关的路径逻辑测试。

打包成 exe 后最容易出的一类 bug 是**「能启动，但数据写到临时目录里」**：
PyInstaller 会把模块解包到 ``sys._MEIPASS``，此时 ``__file__`` 指向那个临时目录，
退出即删。如果数据目录还按 ``__file__`` 推算，用户点「检查更新」写进去的期号
一关窗口就消失了 —— 而且**不报任何错**，是最难发现的那种坏法。

所以这里守住三件事：

1. 冻结运行时，程序根目录必须取 **exe 自己的位置**（``sys.executable``）；
2. 首次运行要把包内的「种子」数据释放到 exe 旁边；
3. **已经存在的数据文件绝不能被种子覆盖** —— 用户自己更新过的数据
   不该因为换了个新 exe 就被上线时的旧快照冲掉。
"""

from __future__ import annotations

import sys

import pytest

from shuangseqiu import data


def test_application_root_is_the_repo_root_in_source_mode() -> None:
    """源码运行时，程序根目录就是仓库根（``picker.py`` 在那儿）。"""
    assert data.application_root() == data.PROJECT_ROOT
    assert (data.PROJECT_ROOT / "picker.py").is_file()
    assert data.DEFAULT_DATA_DIR == data.PROJECT_ROOT / "data"


def test_application_root_follows_the_executable_when_frozen(monkeypatch, tmp_path) -> None:
    """冻结运行时，根目录必须是 exe 所在目录，而不是解包出来的临时目录。"""
    exe_dir = tmp_path / "somewhere" / "else"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "ShuangseqiuPicker.exe"))

    assert data.application_root() == exe_dir.resolve()


def test_bundled_resource_dir_is_none_without_a_bundle(monkeypatch) -> None:
    """源码运行没有包内资源目录，要返回 None 而不是乱猜一个路径。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert data.bundled_resource_dir() is None


def test_bundled_resource_dir_reads_meipass(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert data.bundled_resource_dir() == tmp_path


def _fake_bundle(tmp_path, content: bytes):
    """造一个假的「包内资源目录」，里面放一份种子数据。"""
    bundled = tmp_path / "meipass"
    (bundled / "data").mkdir(parents=True)
    (bundled / "data" / data.DATA_FILE_NAME).write_bytes(content)
    return bundled


def test_seed_copies_the_bundled_snapshot_on_first_run(monkeypatch, tmp_path) -> None:
    """打包后首次运行：exe 旁边还没有数据，就从包内释放一份出来。"""
    bundled = _fake_bundle(tmp_path, b"bundled snapshot")
    target_dir = tmp_path / "app" / "data"
    monkeypatch.setattr(data, "DEFAULT_DATA_DIR", target_dir)
    monkeypatch.setattr(data, "bundled_resource_dir", lambda: bundled)

    result = data.seed_default_data_file()

    assert result == target_dir / data.DATA_FILE_NAME
    assert result.is_file(), "首次运行应当把种子释放到 exe 旁边"
    assert result.read_bytes() == b"bundled snapshot"


def test_seed_never_overwrites_an_existing_workbook(monkeypatch, tmp_path) -> None:
    """关键：用户已经更新过的数据，不能被上线时的旧快照冲掉。"""
    bundled = _fake_bundle(tmp_path, b"old snapshot from build time")
    target_dir = tmp_path / "app" / "data"
    target_dir.mkdir(parents=True)
    existing = target_dir / data.DATA_FILE_NAME
    existing.write_bytes(b"user already updated this")

    monkeypatch.setattr(data, "DEFAULT_DATA_DIR", target_dir)
    monkeypatch.setattr(data, "bundled_resource_dir", lambda: bundled)

    result = data.seed_default_data_file()

    assert result == existing
    assert existing.read_bytes() == b"user already updated this"


def test_seed_in_source_mode_touches_nothing(monkeypatch, tmp_path) -> None:
    """源码运行没有包内资源，只把路径报出去，不创建也不复制任何文件。"""
    target_dir = tmp_path / "data"
    monkeypatch.setattr(data, "DEFAULT_DATA_DIR", target_dir)
    monkeypatch.setattr(data, "bundled_resource_dir", lambda: None)

    result = data.seed_default_data_file()

    assert result == target_dir / data.DATA_FILE_NAME
    assert not target_dir.exists(), "源码运行不该凭空建出一个 data 目录"


def test_seed_points_at_the_real_workbook_in_this_checkout() -> None:
    """本仓库里跑一遍真实路径：应当直接指向仓库内那份工作簿。"""
    result = data.seed_default_data_file()

    assert result == data.PROJECT_ROOT / "data" / data.DATA_FILE_NAME
    assert result.is_file(), f"仓库里应当有数据文件：{result}"


@pytest.mark.parametrize("name", ["application_root", "bundled_resource_dir", "seed_default_data_file"])
def test_packaging_helpers_are_public(name: str) -> None:
    """这几个函数是打包路径的唯一出口，必须是公开 API（前导下划线会没人敢用）。"""
    assert callable(getattr(data, name)), f"data.{name} 应当是可调用的公开函数"
