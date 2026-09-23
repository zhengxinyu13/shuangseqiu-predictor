"""把 ``dist/`` 里的打包产物整理成「可以直接发出去」的压缩包。

为什么要有这一步，而不是让使用者自己去压缩 dist 目录：

1. **dist 目录会被试运行污染**。双击 exe 试一下就会在它旁边生成 ``data/``，
   顺手压缩的话把这个也发出去 —— 对方拿到的就不是「干净电脑」的初始状态了。
   本脚本会**拒绝把顶层 ``data/`` 装进去**，并在组装完成后断言它不存在。
2. **两种形态要配不同的说明**。目录版必须整个文件夹一起拷（``_internal`` 是运行库），
   单文件版只有一个 exe。把说明写错，对方就会「只拷了 exe 然后打不开」。
3. **压缩包里必须有一份面向最终用户的使用说明**，而不是只给一个 exe。

用法（由 build_exe.bat 自动调用）::

    python make_release.py folder     # 目录版 → dist/双色球选号系统[.zip]
    python make_release.py onefile    # 单文件版 → dist/双色球选号系统-单文件版[.zip]
    python make_release.py both       # 两种都出
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
EXE_NAME = "ShuangseqiuPicker.exe"
DATA_DIR_NAME = "data"

# mode -> 分发目录名
RELEASE_NAMES = {
    "folder": "双色球选号系统",
    "onefile": "双色球选号系统-单文件版",
}


def release_files(source: Path) -> list[Path]:
    """列出应当装进压缩包的文件（相对 source 的路径）。

    纯函数，便于测试。规则：

    - 跳过 ``__pycache__`` 之类的噪音；
    - **跳过顶层的 ``data/``** —— 那是试运行留下的，不该发给别人。
      注意只跳顶层：目录版的 ``_internal/data/`` 是程序自带的数据种子，
      **必须带上**，少了它首次运行就没数据可释放。
    """
    picked: list[Path] = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if not path.is_file():
            continue
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        if rel.parts[0] == DATA_DIR_NAME:
            continue
        picked.append(rel)
    return picked


def readme_text(mode: str) -> str:
    """面向最终用户的使用说明。

    **整篇都不能出现「整个文件夹」以外的错话，反过来也一样**：单文件版只有一个 exe，
    任何一处提「文件夹」都会让对方以为漏拷了东西。所以不只是「怎么拷」那一段，
    连「杀毒软件加白名单」这种细节也得跟着改口 —— 这里就是被测试抓到过的第二处。
    """
    if mode == "onefile":
        how_to_copy = """【给第二台电脑用】
就拷这一个 exe 文件即可，别的什么都不用带。
（想省事也可以把它压缩成 zip 再传。）"""
        whitelist = "把这个 exe 加进白名单即可。"
    else:
        how_to_copy = """【给第二台电脑用】
请把 **整个文件夹** 一起拷过去，不要只拷 exe ——
_internal 文件夹是运行库和数据，缺了会打不开。
（想省事也可以把整个文件夹压缩成 zip 再传。）"""
        whitelist = "把整个文件夹加进白名单即可。"

    return f"""双色球选号系统 —— 使用说明
========================================

【怎么运行】
双击 ShuangseqiuPicker.exe 即可。
不需要安装 Python，不需要配置环境，也**不需要联网**。

{how_to_copy}

【第一次运行会自动生成 data 文件夹】
exe 旁边会出现 data\\双色球历史开奖数据_全量.xlsx ——
这份数据是**打包时就装进程序里的**，首次运行自动释放出来，
不需要你提供任何文件、也不会去网上抓。请不要删掉它。

【两个按钮】
· 检查更新 —— 联网抓取最新开奖号码，两个来源逐列比对确认无误后才写入。
              **这一步才需要联网**，大约 3 秒。
· 开始选号 —— 按形态条件抽一注：
              3 奇 3 偶 + 3 大 3 小 + 三区比 2:2:2 + 恰好 1 组二连号
              + 与上一期重号 1 个；蓝球按冷热度加权。
              「复制号码」把结果复制到剪贴板。

【一句话提醒】
这个程序不会改变中奖概率（每注都是 1/17,721,088）。
它只降低「和很多人买到同一组号、中了奖要平分奖金」的概率；
三至六等奖是固定奖金，完全不受影响。请理性购彩。

【长期不用会怎样】
程序自带的数据停在打包那天。搁置超过大约半年才第一次运行的话，
「检查更新」可能因为数据源只保留最近 80 期而无法补齐，选号仍可用但数据偏旧。
遇到这种情况，找发布者要一个新版本即可。

【打不开怎么办】
1) 先看 exe 旁边有没有出现「启动失败.log」，里面写了具体原因。
2) 杀毒软件误报：{whitelist}
   这是 PyInstaller 打包程序的常见误报，并非真的有病毒。
3) 确认 data 文件夹和 exe 在同一层目录下。
"""


def build_release(mode: str) -> int:
    name = RELEASE_NAMES[mode]
    source = DIST / ("ShuangseqiuPicker" if mode == "folder" else "")
    exe = (source / EXE_NAME) if mode == "folder" else (DIST / EXE_NAME)

    if not exe.is_file():
        print(f"[跳过] 找不到 {exe}，先打包这个形态。")
        return 1

    out = DIST / name
    if out.exists():
        shutil.rmtree(out)

    if mode == "folder":
        out.mkdir(parents=True)
        for rel in release_files(source):
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, target)
    else:
        out.mkdir(parents=True)
        shutil.copy2(exe, out / exe.name)

    (out / "使用说明.txt").write_text(readme_text(mode), encoding="utf-8-sig", newline="\r\n")

    # 组装完再确认一次：顶层绝不能有 data/，否则说明有东西漏进来了。
    stray = out / DATA_DIR_NAME
    if stray.exists():
        print(f"[错误] 分发目录里出现了顶层 {DATA_DIR_NAME}/，已中止以免把试运行的数据发出去。")
        return 1

    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(out.parent))

    files = [p for p in out.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"[完成] {name}/  {len(files)} 个文件，{total / 1048576:.1f} MB")
    print(f"       {zip_path.name}  {zip_path.stat().st_size / 1048576:.1f} MB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="组装可分发压缩包")
    parser.add_argument("mode", choices=[*RELEASE_NAMES, "both"], nargs="?", default="both")
    args = parser.parse_args()

    modes = list(RELEASE_NAMES) if args.mode == "both" else [args.mode]
    results = [build_release(mode) for mode in modes]
    # 只出了一种也不当失败：两种形态本来就允许只打一种
    return 0 if any(code == 0 for code in results) else 1


if __name__ == "__main__":
    sys.exit(main())
