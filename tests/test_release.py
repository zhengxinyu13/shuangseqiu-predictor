"""分发压缩包组装（``make_release.py``）的测试。

这一步看起来只是「拷文件 + 压缩」，其实有两个真会出错的地方：

1. **顶层 ``data/`` 必须排除，``_internal/data/`` 必须保留。**
   前者是试运行 exe 留下的痕迹（双击一下就会生成），发出去对方拿到的
   就不是「干净电脑」的初始状态；后者是程序自带的数据种子，**少了它首次运行
   就没数据可释放**。一字之差，一个多发了、一个漏发了，而产物看起来都"正常"。
2. **两种形态的「怎么拷」必须写对。** 目录版只拷 exe 会缺 ``_internal`` 打不开；
   单文件版说「整个文件夹一起拷」会让对方以为漏了东西。写反了对方就卡在这。

另外守住一句最关键的话：使用说明必须讲清**数据是随包带的，不是联网抓的** ——
这正是真实用户会问的问题（"没有源文件怎么复制？"）。
"""

from __future__ import annotations

import make_release


def _fake_dist(tmp_path):
    """造一个「试运行过」的目录版产物：顶层有 data/，_internal/data/ 里也有。"""
    source = tmp_path / "ShuangseqiuPicker"
    (source / "_internal" / "data").mkdir(parents=True)
    (source / "_internal" / "__pycache__").mkdir(parents=True)

    (source / "ShuangseqiuPicker.exe").write_bytes(b"exe")
    (source / "_internal" / "python313.dll").write_bytes(b"dll")
    (source / "_internal" / "data" / "seed.xlsx").write_bytes(b"seed")
    (source / "_internal" / "__pycache__" / "junk.pyc").write_bytes(b"junk")
    # 试运行留下的：双击 exe 就会在它旁边生成
    (source / "data").mkdir()
    (source / "data" / "user-data.xlsx").write_bytes(b"leftover from a test run")
    return source


def test_release_files_drops_a_stray_toplevel_data_dir(tmp_path):
    """顶层 data/ 是试运行留下的，不能发给别人。"""
    source = _fake_dist(tmp_path)
    picked = {str(p) for p in make_release.release_files(source)}

    assert "ShuangseqiuPicker.exe" in picked
    assert not any(p.startswith("data") for p in picked), picked


def test_release_files_keeps_the_bundled_seed_inside_internal(tmp_path):
    """关键：只排除**顶层** data/。_internal/data/ 是程序自带的种子，必须带上。"""
    source = _fake_dist(tmp_path)
    picked = {str(p).replace("\\", "/") for p in make_release.release_files(source)}

    assert "_internal/data/seed.xlsx" in picked, "自带的数据种子被误删了，首次运行会没数据"


def test_release_files_skips_bytecode_noise(tmp_path):
    source = _fake_dist(tmp_path)
    picked = {str(p).replace("\\", "/") for p in make_release.release_files(source)}

    assert not any("__pycache__" in p for p in picked), picked
    assert not any(p.endswith(".pyc") for p in picked), picked


def test_release_files_returns_relative_paths_only(tmp_path):
    source = _fake_dist(tmp_path)
    for path in make_release.release_files(source):
        assert not path.is_absolute(), path
        assert (source / path).is_file(), path


def test_folder_readme_tells_the_user_to_copy_the_whole_folder():
    text = make_release.readme_text("folder")

    assert "整个文件夹" in text
    assert "_internal" in text, "目录版必须提醒 _internal 是运行库，缺了打不开"


def test_onefile_readme_tells_the_user_one_file_is_enough():
    text = make_release.readme_text("onefile")

    assert "只拷这一个 exe" in text or "就拷这一个" in text
    assert "整个文件夹" not in text, "单文件版不该说「整个文件夹」，会让人以为漏拷了东西"


def test_readme_states_the_data_ships_inside_the_app():
    """真实用户会问「别的电脑没有源文件，怎么复制？」——说明里必须先讲清。"""
    for mode in ("folder", "onefile"):
        text = make_release.readme_text(mode)
        assert "装进程序里" in text or "随程序" in text, mode
        assert "不需要你提供任何文件" in text, mode


def test_readme_is_crlf_friendly_for_notepad() -> None:
    """说明文件是给普通用户用记事本打开的，别出现裸 LF 之外的意外字符。"""
    for mode in ("folder", "onefile"):
        text = make_release.readme_text(mode)
        assert "\r" not in text, "换行统一在写盘时用 newline='\\r\\n' 处理，文本本身不该含 \\r"
        assert not text.startswith("\ufeff"), "BOM 由写盘时的 utf-8-sig 负责"


def test_readme_warns_about_the_long_idle_window() -> None:
    """搁置太久数据补不上这件事，要写在说明里，免得用户以为是程序坏了。"""
    for mode in ("folder", "onefile"):
        text = make_release.readme_text(mode)
        assert "检查更新" in text
        assert "80 期" in text or "半年" in text, mode


def test_release_names_are_distinct_per_mode() -> None:
    """两种形态不能装进同一个目录，否则压缩包会互相覆盖。"""
    names = set(make_release.RELEASE_NAMES.values())
    assert len(names) == len(make_release.RELEASE_NAMES)
