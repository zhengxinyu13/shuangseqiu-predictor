"""``picker.py``（Tkinter 界面）的静态契约测试。

这个文件**不导入 picker.py**，只解析它的源码 —— 因为测试环境（`default` venv）
里没有 tkinter，一旦 import 就会 ImportError。界面的运行时行为由
``.workbuddy/tools/ui_smoke.py`` 负责（那个脚本必须用带 tkinter 的解释器跑），
这里只管**结构上不许违反的约定**：

1. **界面不许引入科学计算栈。** `picker.py` 只在 `ssq-picker` 环境里跑，
   那个环境只装了 openpyxl + httpx。一旦它（或它依赖的模块）碰了
   numpy / scipy / matplotlib，界面在用户机器上就会直接起不来。
   这条约束以前只是写在文档里，现在有测试守。
2. **源码必须能被解析。** pytest 永远不会 import 这个文件，所以它里面
   混进一个语法错误，整套测试都不会红 —— 只有用户双击 .bat 才发现。
3. **署名文案与窗口尺寸常量确实存在、且署名真的被用到了**，
   免得哪天把常量删了留下一个孤儿定义。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PICKER = Path(__file__).resolve().parents[1] / "picker.py"

# 界面允许的第三方依赖。`ssq-picker` 环境里就这两个（外加标准库 tkinter）。
ALLOWED_THIRD_PARTY = {"openpyxl", "httpx"}

# 一方包：本仓库自己的代码，不算第三方依赖。
FIRST_PARTY = {"shuangseqiu"}

# 界面的业务模块白名单。stats/charts/report 分别会拉起 scipy 与 matplotlib，
# 必须留在报告侧，不能被界面间接引入。
ALLOWED_BUSINESS_MODULES = {"data", "dataset", "selector", "updater"}

# 署名是 Grayson 指定的文案，属于「不改的史实」——写死并配哨兵。
EXPECTED_FOOTER = "This software was written by Grayson Zheng."


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    """解析 picker.py 得到语法树（不执行、不导入）。"""
    source = PICKER.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(PICKER))


@pytest.fixture(scope="module")
def source() -> str:
    return PICKER.read_text(encoding="utf-8")


def _top_level_names(module_name: str) -> str:
    """把 ``a.b.c`` 收敛成顶层包名 ``a``。"""
    return module_name.split(".", 1)[0]


def test_picker_parses(tree: ast.Module) -> None:
    """语法必须正确 —— pytest 不会 import 它，这是唯一的防线。"""
    assert isinstance(tree, ast.Module)
    assert tree.body, "picker.py 不该是空文件"


def test_picker_stays_off_the_scientific_stack(tree: ast.Module) -> None:
    """界面只许依赖 openpyxl + httpx（+ 标准库），不许碰 numpy/scipy/matplotlib。

    这不是风格问题：`ssq-picker` 环境里根本没装科学计算栈，
    一旦引入，用户双击 run_picker.bat 就会 ImportError。
    """
    third_party: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            third_party.update(_top_level_names(a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            third_party.add(_top_level_names(node.module))

    extra = {
        name
        for name in third_party
        if name not in sys.stdlib_module_names and name not in FIRST_PARTY
    }
    assert extra <= ALLOWED_THIRD_PARTY, (
        f"picker.py 引入了界面环境没有的第三方依赖：{sorted(extra - ALLOWED_THIRD_PARTY)}。"
        f"界面只能依赖 {sorted(ALLOWED_THIRD_PARTY)} 加标准库。"
    )


def test_picker_only_uses_the_lightweight_business_modules(tree: ast.Module) -> None:
    """业务包只许从 data/dataset/selector/updater 里取，不许碰 stats/charts/report。"""
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] != "shuangseqiu":
                continue
            if len(parts) > 1:  # from shuangseqiu.dataset import ...
                used.add(parts[1])
            else:  # from shuangseqiu import dataset, selector, ...
                used.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "shuangseqiu" and len(parts) > 1:
                    used.add(parts[1])

    assert used, "没解析出任何业务模块，说明 ImportFrom 的解析逻辑失效了"
    over = used - ALLOWED_BUSINESS_MODULES
    assert not over, (
        f"picker.py 用了不该用的业务模块：{sorted(over)}。"
        f"stats/charts/report 会拉起 scipy 与 matplotlib，只能留在报告侧。"
    )


def _module_constant(tree: ast.Module, name: str) -> object:
    """取出模块级 ``NAME = <字面量>`` 的值；取不到返回 None。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    return None
    return None


def test_footer_credit_is_exactly_the_requested_text(tree: ast.Module) -> None:
    """署名文案是 Grayson 指定的，锁死。"""
    assert _module_constant(tree, "FOOTER") == EXPECTED_FOOTER


def test_footer_credit_is_ascii(tree: ast.Module) -> None:
    """全 ASCII —— 免受编辑器/终端编码影响，也是唯一不会乱码的写法。"""
    footer = _module_constant(tree, "FOOTER")
    assert isinstance(footer, str)
    assert footer.isascii(), f"署名里出现了非 ASCII 字符：{footer!r}"


def test_footer_constant_is_not_an_orphan(tree: ast.Module) -> None:
    """FOOTER 必须真的被渲染出来，不能只是躺在文件顶部。"""
    loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "FOOTER"
        and isinstance(node.ctx, ast.Load)
    ]
    assert loads, "FOOTER 常量定义了却没人用，页脚其实没画出来"


def test_footer_is_the_last_grid_row(source: str) -> None:
    """页脚要出现在 _build_widgets 的末尾、且是行号最大的那个。

    用行号做粗粒度定位（精确的几何断言在 ui_smoke.py 里做，
    这里只需要保证「加页脚时没把它插到中间去」）。
    """
    body = source.split("def _build_widgets")[1].split("def ")[0]
    assert "row=6" in body, "页脚没落在预期的最后一行（row 6）"
    assert "Separator" in body, "页脚上方缺少分隔线"
    # row 6 必须是出现过的最大行号
    rows = [int(line.split("row=")[1].split(",")[0]) for line in body.splitlines() if "row=" in line]
    assert rows, "没解析到任何 grid 行号"
    assert max(rows) == 6, f"页脚不是最后一行，最大 row={max(rows)}"


def test_window_size_constants_are_sane(tree: ast.Module) -> None:
    """窗口尺寸常量给截图工具复用，必须是两个正整数。"""
    size = _module_constant(tree, "WIN_SIZE")
    minimum = _module_constant(tree, "WIN_MIN_SIZE")
    assert isinstance(size, tuple) and len(size) == 2
    assert all(isinstance(v, int) and v > 0 for v in size), size
    assert isinstance(minimum, tuple) and len(minimum) == 2
    assert all(minimum[i] <= size[i] for i in range(2)), (
        f"最小尺寸 {minimum} 不该大于默认尺寸 {size}"
    )
