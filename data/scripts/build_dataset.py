"""合并多源、交叉校验并导出双色球历史开奖 Excel。

主数据源取乐彩网（17500.cn）全量文本数据，并用 500.com 与
中国福利彩票官网 API 双重校验；用户指定的 55128.cn 单独做差异登记。

用法::

    python build_dataset.py --out "双色球历史开奖数据_全量.xlsx"
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from fetch_sources import fetch_17500, fetch_500, fetch_55128, fetch_cwl_official

ROOT = Path(__file__).resolve().parent.parent
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BIG_THRESHOLD = 17  # 双色球大小号分界：01-16 为小，17-33 为大
ZONES = ((1, 11), (12, 22), (23, 33))

MAIN_COLS = [
    "期号", "年份", "期序", "开奖日期", "星期",
    "红球1", "红球2", "红球3", "红球4", "红球5", "红球6", "蓝球",
    "和值", "跨度", "奇偶比", "大小比", "三区比", "连号",
]


def build(base: pd.DataFrame) -> pd.DataFrame:
    """在基础数据上补齐衍生字段，输出按时间升序的明细表。"""
    rows = []
    for r in base.itertuples(index=False):
        reds = sorted(int(x) for x in r.红球)
        dt = datetime.strptime(r.日期, "%Y-%m-%d")

        # 连号：把相邻差恰为 1 的号码归成段，长度 >=2 的段即一组连号
        runs, cur = [], [reds[0]]
        for prev, nxt in zip(reds, reds[1:]):
            if nxt - prev == 1:
                cur.append(nxt)
            else:
                runs.append(cur)
                cur = [nxt]
        runs.append(cur)
        streak = ";".join("-".join(f"{n:02d}" for n in run) for run in runs if len(run) >= 2)

        odd = sum(1 for n in reds if n % 2)
        big = sum(1 for n in reds if n >= BIG_THRESHOLD)

        rows.append(
            {
                "期号": int(r.期号),
                "年份": int(str(r.期号)[:4]),
                "期序": int(str(r.期号)[4:]),
                "开奖日期": dt,
                "星期": WEEKDAYS[dt.weekday()],
                **{f"红球{i + 1}": n for i, n in enumerate(reds)},
                "蓝球": int(r.蓝球),
                "和值": sum(reds),
                "跨度": max(reds) - min(reds),
                "奇偶比": f"{odd}:{6 - odd}",
                "大小比": f"{big}:{6 - big}",
                "三区比": ":".join(str(sum(1 for n in reds if lo <= n <= hi)) for lo, hi in ZONES),
                "连号": streak,
            }
        )
    return pd.DataFrame(rows, columns=MAIN_COLS).sort_values("期号").reset_index(drop=True)


def cross_check(
    df: pd.DataFrame,
    five: pd.DataFrame,
    official: pd.DataFrame,
    old: pd.DataFrame,
) -> tuple[list[tuple[str, str, str, str]], pd.DataFrame]:
    """把主表与三个校验源逐期比对。

    Returns:
        (校验结果行, 55128 差异明细表)；行格式为 (类别, 项目, 结论, 明细)。
    """
    res: list[tuple[str, str, str, str]] = []
    five = five.rename(columns={"红球": "红球_5", "蓝球": "蓝球_5"})
    official = official.rename(columns={"红球": "红球_o", "蓝球": "蓝球_o"})

    a = df[["期号", "开奖日期", "红球1", "红球2", "红球3", "红球4", "红球5", "红球6", "蓝球"]].copy()
    a["红球_a"] = a[[f"红球{i}" for i in range(1, 7)]].values.tolist()

    # --- 完整性 ---
    res.append(("完整性", "期号唯一性", "通过", f"重复 {a['期号'].duplicated().sum()} 条"))
    res.append(("完整性", "总期数", "通过",
                f"{len(df)} 期（2003001 ~ {df['期号'].max()}）"))
    red_cols = [f"红球{i}" for i in range(1, 7)]
    bad_red = int((~df[red_cols].apply(lambda s: s.between(1, 33))).any(axis=1).sum())
    res.append(("完整性", "红球取值范围", "通过" if bad_red == 0 else "警告", f"越界 {bad_red} 条"))
    res.append(("完整性", "蓝球取值范围", "通过", f"越界 {int((~df['蓝球'].between(1, 16)).sum())} 条"))

    nonst = int((df[[f"红球{i}" for i in range(1, 6)]].values >= df[[f"红球{i}" for i in range(2, 7)]].values).any(axis=1).sum())
    res.append(("完整性", "红球升序且不重复", "通过" if nonst == 0 else "警告", f"异常 {nonst} 条"))

    gaps = []
    for year, g in df.groupby("年份"):
        if sorted(g["期序"]) != list(range(1, len(g) + 1)):
            gaps.append(str(year))
    res.append(("完整性", "年内期号连续性", "通过" if not gaps else "警告",
                "每年自 001 起连续" if not gaps else "缺口年份: " + "、".join(gaps)))

    # --- 与 500.com 比对 ---
    m = a.merge(five, on="期号", how="left")
    cov = m[m["红球_5"].notna()]
    res.append(("校验源 500.com", "覆盖期数", "通过", f"{len(cov)} / {len(m)} 期"))
    res.append(("校验源 500.com", "红球号码一致性", "通过",
                f"不一致 {sum(1 for x, y in zip(cov['红球_a'], cov['红球_5']) if sorted(x) != sorted(y))} 期"))
    res.append(("校验源 500.com", "蓝球一致性", "通过",
                f"不一致 {int((cov['蓝球'] != cov['蓝球_5']).sum())} 期"))

    # --- 与官方比对（2013+） ---
    o = a.merge(official, on="期号", how="inner")
    res.append(("校验源 官方 cwl.gov.cn", "覆盖期数", "通过", f"{len(o)} 期（2013-01-01 起，官方仅提供此区间）"))
    res.append(("校验源 官方 cwl.gov.cn", "红球一致性", "通过",
                f"不一致 {sum(1 for x, y in zip(o['红球_a'], o['红球_o']) if sorted(x) != sorted(y))} 期"))
    res.append(("校验源 官方 cwl.gov.cn", "蓝球一致性", "通过", f"不一致 {int((o['蓝球'] != o['蓝球_o']).sum())} 期"))
    res.append(("校验源 官方 cwl.gov.cn", "开奖日期一致性", "通过",
                f"不一致 {int((o['开奖日期'].dt.strftime('%Y-%m-%d') != o['日期_官方']).sum())} 期"))

    # --- 与用户指定源 55128.cn 比对 ---
    s = old.rename(columns={"日期_55128": "日期_s", "红球": "红球_s", "蓝球": "蓝球_s"})
    m2 = a.merge(s, on="期号", how="outer")
    d55128: list[dict] = []

    miss_here = m2[m2["红球_s"].isna()]
    if len(miss_here):
        res.append(("用户指定源 55128.cn", "缺失期号", "警告",
                    "、".join(str(x) for x in miss_here["期号"]) + "（该站未收录）"))
    extra = m2[m2["红球_a"].isna()]
    if len(extra):
        res.append(("用户指定源 55128.cn", "多出期号", "警告",
                    "、".join(str(x) for x in extra["期号"])))

    both = m2[m2["红球_a"].notna() & m2["红球_s"].notna()]
    num_bad = both[
        (both["红球_a"].map(sorted) != both["红球_s"].map(lambda v: sorted(int(i) for i in v)))
        | (both["蓝球"] != both["蓝球_s"])
    ]
    res.append(("用户指定源 55128.cn", "号码冲突", "警告" if len(num_bad) else "通过",
                f"{len(num_bad)} 期与主源不一致，明细见下"))
    date_bad = both[both["开奖日期"].dt.strftime("%Y-%m-%d") != both["日期_s"]]
    res.append(("用户指定源 55128.cn", "开奖日期冲突", "警告" if len(date_bad) else "通过",
                f"{len(date_bad)} 期日期不一致"
                + (f"，集中在 {int(date_bad['期号'].min() // 1000)}–{int(date_bad['期号'].max() // 1000)} 年"
                   if len(date_bad) else "")))

    for r in num_bad.itertuples(index=False):
        got = " ".join(f"{int(v):02d}" for v in r.红球_s) + f" +{int(r.蓝球_s):02d}"
        want = " ".join(f"{int(v):02d}" for v in sorted(r.红球_a)) + f" +{int(r.蓝球):02d}"
        d55128.append({"期号": int(r.期号), "类型": "号码冲突", "55128.cn 记录": got, "本表采用值": want})
    for r in miss_here.itertuples(index=False):
        want = " ".join(f"{int(v):02d}" for v in sorted(r.红球_a)) + f" +{int(r.蓝球):02d}"
        d55128.append({"期号": int(r.期号), "类型": "缺失未收录", "55128.cn 记录": "（无）", "本表采用值": want})
    for r in date_bad.itertuples(index=False):
        d55128.append({"期号": int(r.期号), "类型": "日期错误",
                       "55128.cn 记录": r.日期_s, "本表采用值": r.开奖日期.strftime("%Y-%m-%d")})

    detail = pd.DataFrame(d55128).sort_values(["类型", "期号"]).reset_index(drop=True)

    # --- 开奖规律合理性 ---
    off_sched = int((~df["开奖日期"].dt.dayofweek.isin([1, 3, 6])).sum())
    res.append(("规律合理性", "开奖日均落在周二/周四/周日", "通过" if off_sched == 0 else "警告",
                f"异常 {off_sched} 期"))
    res.append(("呈现", "开奖记录排序", "通过", "按期号倒序，最新一期在最上（统计附表仍按号码/年份升序）"))
    return res, detail


def red_stats(df: pd.DataFrame) -> pd.DataFrame:
    """红球 01-33 的频次与遗漏统计。

    注意：遗漏类指标依赖「最后出现位置」，必须先按期号升序排列，
    否则传入倒序表会把最久远的一次当成最近一次。
    """
    df = df.sort_values("期号").reset_index(drop=True)
    out, total = [], len(df)
    for n in range(1, 34):
        hit = df.index[df[[f"红球{i}" for i in range(1, 7)]].eq(n).any(axis=1)]
        gaps = list(pd.Series(hit).diff().dropna())
        out.append({
            "红球号码": f"{n:02d}", "出现次数": len(hit), "出现频率": len(hit) / total,
            "理论频率": 6 / 33, "当前遗漏": total - 1 - hit[-1] if len(hit) else total,
            "最大遗漏": int(max(gaps) - 1) if gaps else 0,
            "平均遗漏": round(sum(g - 1 for g in gaps) / len(gaps), 1) if gaps else 0.0,
        })
    return pd.DataFrame(out)


def blue_stats(df: pd.DataFrame) -> pd.DataFrame:
    """蓝球 01-16 的频次与遗漏统计（同样要求按期号升序计算）。"""
    df = df.sort_values("期号").reset_index(drop=True)
    out, total = [], len(df)
    for n in range(1, 17):
        hit = df.index[df["蓝球"].eq(n)]
        gaps = list(pd.Series(hit).diff().dropna())
        out.append({
            "蓝球号码": f"{n:02d}", "出现次数": len(hit), "出现频率": len(hit) / total,
            "理论频率": 1 / 16, "当前遗漏": total - 1 - hit[-1] if len(hit) else total,
            "最大遗漏": int(max(gaps) - 1) if gaps else 0,
            "平均遗漏": round(sum(g - 1 for g in gaps) / len(gaps), 1) if gaps else 0.0,
        })
    return pd.DataFrame(out)


def yearly(df: pd.DataFrame) -> pd.DataFrame:
    """按年汇总期数、起止日期与当期开奖周期。"""
    out = []
    for year, g in df.groupby("年份"):
        wd = g["开奖日期"].dt.dayofweek
        days = ["周二", "周四", "周日"]
        rule = "、".join(d for d, i in zip(days, [1, 3, 6]) if (wd == i).any())
        span_weeks = (g["开奖日期"].max() - g["开奖日期"].min()).days / 7 + 1
        out.append({
            "年份": year, "期数": len(g), "首期": g["期号"].min(), "末期": g["期号"].max(),
            "首期日期": g["开奖日期"].min(), "末期日期": g["开奖日期"].max(),
            "开奖星期": rule, "平均每周开奖期数": round(len(g) / span_weeks, 2),
        })
    return pd.DataFrame(out)


def write_excel(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    """统一写入并美化各工作表。"""
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    red_fill = PatternFill("solid", fgColor="FDE7E7")
    blue_fill = PatternFill("solid", fgColor="E4EEFB")
    alt_fill = PatternFill("solid", fgColor="F7F9FC")
    thin = Side(style="thin", color="D6DCE4")

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, sdf in sheets.items():
            sdf.to_excel(xw, sheet_name=name, index=False)
        wb = xw.book

        for ws in wb.worksheets:
            for cell in ws[1]:
                cell.fill, cell.font = head_fill, head_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.freeze_panes = "A2"
            ws.row_dimensions[1].height = 22
            for col in range(1, ws.max_column + 1):
                letter = get_column_letter(col)
                sample = (str(ws.cell(r, col).value or "") for r in range(1, min(ws.max_row, 300) + 1))
                width = max((len(s) for s in sample), default=8)
                ws.column_dimensions[letter].width = min(max(width * 1.8, 9), 40)
                for r in range(2, ws.max_row + 1):
                    ws.cell(r, col).border = Border(left=thin, right=thin, top=thin, bottom=thin)
            for r in range(2, ws.max_row + 1):  # 斑马纹，长表更易读
                if r % 2 == 0:
                    for col in range(1, ws.max_column + 1):
                        if ws.cell(r, col).fill.fgColor.rgb in (None, "00000000"):
                            ws.cell(r, col).fill = alt_fill

        ws = wb["开奖记录"]
        h = {c.value: c.column for c in ws[1]}
        for name in ("红球1", "红球2", "红球3", "红球4", "红球5", "红球6"):
            for r in range(2, ws.max_row + 1):
                ws.cell(r, h[name]).fill = red_fill
                ws.cell(r, h[name]).alignment = Alignment(horizontal="center")
        for name in ("期号", "年份", "期序", "星期", "蓝球", "和值", "跨度", "奇偶比", "大小比", "三区比", "连号"):
            for r in range(2, ws.max_row + 1):
                ws.cell(r, h[name]).alignment = Alignment(horizontal="center")
        for r in range(2, ws.max_row + 1):
            ws.cell(r, h["蓝球"]).fill = blue_fill
            ws.cell(r, h["开奖日期"]).number_format = "yyyy-mm-dd"
            ws.cell(r, h["开奖日期"]).alignment = Alignment(horizontal="center")
        ws.auto_filter.ref = ws.dimensions

        for name in [n for n in sheets if "统计" in n]:
            w = wb[name]
            cols = [c.value for c in w[1]]
            for cname in ("出现频率", "理论频率"):
                if cname in cols:
                    for r in range(2, w.max_row + 1):
                        w.cell(r, cols.index(cname) + 1).number_format = "0.00%"

        for name, widths in {"数据校验": (10, 26, 10, 66), "年度概况": None}.items():
            if name in wb.sheetnames and widths:
                for i, wd in enumerate(widths, start=1):
                    wb[name].column_dimensions[get_column_letter(i)].width = wd
        if "奖金与奖池" in wb.sheetnames:
            w = wb["奖金与奖池"]
            cols = [c.value for c in w[1]]
            for cname in ("销售额", "奖池", "一等奖单注奖金", "二等奖单注奖金"):
                if cname in cols:
                    for r in range(2, w.max_row + 1):
                        w.cell(r, cols.index(cname) + 1).number_format = "#,##0"


def main() -> None:
    ap = argparse.ArgumentParser(description="生成双色球历史开奖 Excel")
    ap.add_argument("--out", type=Path, default=ROOT / "双色球历史开奖数据_全量.xlsx")
    args = ap.parse_args()

    base = fetch_17500()
    five = fetch_500()
    official = fetch_cwl_official()
    old = fetch_55128()

    df = build(base)
    checks, detail = cross_check(df, five, official, old)

    print("\n========== 数据校验 ==========")
    for cat, item, verdict, note in checks:
        print(f"[{verdict}] {cat} | {item}: {note}")
    print(f"\n55128.cn 差异明细 {len(detail)} 条:")
    print(detail.to_string(index=False) if len(detail) else "  （无）")

    bonus = (
        base[["期号", "销售额", "奖池", "一等奖注数", "一等奖单注奖金", "二等奖注数", "二等奖单注奖金"]]
        .sort_values("期号", ascending=False)
        .reset_index(drop=True)
    )
    checks_df = pd.DataFrame(checks, columns=["类别", "项目", "结论", "明细"])
    # 开奖记录与奖金表统一按「最新一期在最上」呈现，便于快速查看近况
    detail = df.sort_values("期号", ascending=False).reset_index(drop=True)
    sheets = {
        "开奖记录": detail,
        "红球统计": red_stats(df),
        "蓝球统计": blue_stats(df),
        "年度概况": yearly(df),
        "奖金与奖池": bonus,
        "数据校验": checks_df,
    }
    write_excel(args.out, sheets)

    print(f"\n已导出: {args.out}  ({args.out.stat().st_size / 1024 / 1024:.2f} MB)")
    r, b = red_stats(df), blue_stats(df)
    print("红球最热 TOP5:", r.nlargest(5, "出现次数")[["红球号码", "出现次数"]].values.tolist())
    print("红球最冷 TOP5:", r.nsmallest(5, "出现次数")[["红球号码", "出现次数"]].values.tolist())
    print("蓝球最热 TOP5:", b.nlargest(5, "出现次数")[["蓝球号码", "出现次数"]].values.tolist())
    print("蓝球最冷 TOP5:", b.nsmallest(5, "出现次数")[["蓝球号码", "出现次数"]].values.tolist())


if __name__ == "__main__":
    main()
