# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义：把「双色球选号系统」压成 Windows 独立 exe。

产物**不需要目标机器装 Python**，双击即用。

正常用 ``build_exe.bat`` 调用（它会先把打包环境备好）。手动调用：

    pyinstaller --noconfirm --clean build_exe.spec                 :: 目录版
    set SSQ_ONEFILE=1 && pyinstaller --noconfirm --clean build_exe.spec   :: 单文件版

两种形态怎么选：

- **目录版（默认，推荐）**：产出 ``dist/ShuangseqiuPicker/`` 一个文件夹，exe 在里面。
  启动快（不往临时目录解包），杀软误报明显更少，出问题也好排查。
- **单文件版**：只有一个 exe，发给别人最省事；代价是每次启动都要解包一份
  （慢 2~5 秒），并且更容易被杀软误报。

**为什么可以放心裁掉 numpy / scipy / matplotlib**：界面那条依赖链只用到
tkinter + openpyxl + httpx，科学计算栈只装在报告环境里，``picker.py`` 根本不 import
（``tests/test_ui_contract.py`` 就是守这条的）。裁掉之后体积从上百 MB 降到几十 MB。
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
ONEFILE = os.environ.get("SSQ_ONEFILE") == "1"
APP_NAME = "ShuangseqiuPicker"

# 把历史数据作为「种子」打进包里。首次运行时 data.seed_default_data_file()
# 会把它释放到 exe 旁边，此后「检查更新」写的就是那个副本 ——
# 用户自己更新的期号不会因为换一个 exe 就被覆盖掉。
datas = [(str(ROOT / "data" / "双色球历史开奖数据_全量.xlsx"), "data")]

a = Analysis(
    [str(ROOT / "picker.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 报告侧专属，界面用不到，别让它们混进来撑体积
        "numpy",
        "scipy",
        "matplotlib",
        "pandas",
        "pytest",
        "PIL",
        "lxml",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

common = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI 程序，不要那个黑窗口
    disable_windowed_traceback=False,  # 崩溃时保留可读回溯，picker 会弹窗展示
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
