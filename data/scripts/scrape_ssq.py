"""爬取 55128.cn 双色球历史开奖数据（2003-01-02 至今）。

数据源: https://www.55128.cn/kjh/fcssq-history-80.htm?year=YYYY
站点通过 ``?year=`` 查询参数整年返回，与路径里的 30/50/80/120 无关。

用法::

    python scrape_ssq.py --out ../_raw/years --start 2003 --end 2026
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import TypedDict

import httpx

BASE = "https://www.55128.cn/kjh/fcssq-history-80.htm"
HEADERS = {
    # 站点未做严格反爬，普通桌面 UA + Referer 即可直出完整静态表格
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": BASE,
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class DrawRow(TypedDict):
    """一行开奖记录（原始字段，未做衍生计算）。"""

    year: int
    date: str
    issue: str
    reds: list[int]
    blue: int
    machine: str  # 开机号，形如 "03,08,17,22,29,30+09"，老数据可能为空
    raw_sum: int | None  # 站点给出的和值
    raw_odd_even: str | None  # 站点给出的奇偶比
    raw_big_small: str | None  # 站点给出的大小比
    raw_span: int | None  # 站点给出的跨度


_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_BALL = re.compile(r'class="ball-list\s+(red|kjhblue)">\s*(\d{2})\s*</span>')
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISSUE = re.compile(r"^\d{7}$")
_MACHINE = re.compile(r"^\s*[\d,]{17}\+\d{2}\s*$")


def _clean(html_fragment: str) -> str:
    """剥掉标签与多余空白，返回纯文本。"""
    txt = re.sub(r"<[^>]+>", "", html_fragment)
    return txt.replace("\xa0", " ").strip()


def fetch_year(client: httpx.Client, year: int, cache_dir: Path) -> str:
    """抓取指定年份的整年开奖列表 HTML，带本地缓存。

    Args:
        client: 复用的 httpx 客户端。
        year: 年份，如 2020。
        cache_dir: HTML 缓存目录；已存在则直接读缓存，避免重复请求。

    Returns:
        页面 HTML 文本。

    Raises:
        httpx.HTTPStatusError: 站点返回非 2xx 时抛出。
    """
    cache = cache_dir / f"year_{year}.html"
    if cache.exists() and cache.stat().st_size > 5000:
        return cache.read_text(encoding="utf-8")

    resp = client.get(BASE, params={"year": year})
    resp.raise_for_status()
    html = resp.content.decode("utf-8", "ignore")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding="utf-8")
    return html


def parse_year(html: str, year: int) -> list[DrawRow]:
    """把单年列表页解析成结构化记录。

    列顺序: 开奖时间 / 期数 / 号码 / (开机号) / 和值 / 单双 / 奇偶比 /
    大小比 / 奇偶形态 / 跨度 / 其他。开机号为后期新增列，老数据可能缺列，
    因此不用固定下标，而是按内容特征归类。

    Args:
        html: 年份列表页 HTML。
        year: 该页对应年份。

    Returns:
        该年所有开奖记录，按站点顺序（倒序）。
    """
    rows: list[DrawRow] = []

    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        balls = _BALL.findall(tr)
        if len(balls) != 7:
            continue

        reds = sorted(int(v) for kind, v in balls if kind == "red")
        blues = [int(v) for kind, v in balls if kind == "kjhblue"]
        if len(reds) != 6 or len(blues) != 1:
            continue

        cells = [_clean(c) for c in _TD.findall(tr)]
        cells = [c for c in cells if c]

        date = next((c for c in cells if _DATE.match(c)), "")
        issue = next((c for c in cells if _ISSUE.match(c)), "")
        if not date or not issue:
            continue

        # 号码单元格之后的剩余文本，按序即 开机号/和值/单双/奇偶比/大小比/形态/跨度/其他
        raw_tds = _TD.findall(tr)
        ball_idx = next(i for i, c in enumerate(raw_tds) if _BALL.search(c))
        rest = [_clean(c) for c in raw_tds[ball_idx + 1 :]]
        rest = [c for c in rest if c]

        machine = ""
        if rest and _MACHINE.match(rest[0]):
            machine = rest[0].replace(" ", "")
            rest = rest[1:]
        # 末尾是「详情 / 走势图」链接列
        rest = [c for c in rest if c not in {"详情", "走势图"}]

        ratios = [c for c in rest if re.fullmatch(r"\d:\d", c)]
        raw_sum = next((int(c) for c in rest if re.fullmatch(r"\d{2,3}", c)), None)
        raw_oe = ratios[0] if len(ratios) > 0 else None
        raw_bs = ratios[1] if len(ratios) > 1 else None
        raw_span = None
        # 跨度是「形态列」之后的最后一个纯数字
        form_idx = next((i for i, c in enumerate(rest) if "," in c), -1)
        if form_idx >= 0:
            for c in rest[form_idx + 1 :]:
                if re.fullmatch(r"\d{1,2}", c):
                    raw_span = int(c)
                    break

        rows.append(
            DrawRow(
                year=year,
                date=date,
                issue=issue,
                reds=reds,
                blue=blues[0],
                machine=machine,
                raw_sum=raw_sum,
                raw_odd_even=raw_oe,
                raw_big_small=raw_bs,
                raw_span=raw_span,
            )
        )

    return rows


def scrape(start: int, end: int, cache_dir: Path, delay: float = 0.6) -> list[DrawRow]:
    """抓取并解析 [start, end] 闭区间内每一年的开奖数据。

    Args:
        start: 起始年份。
        end: 结束年份（含）。
        cache_dir: HTML 缓存目录。
        delay: 每次网络请求之间的间隔秒数，礼貌抓取。

    Returns:
        合并后的全部记录（按年份升序，年内保持站点倒序）。
    """
    out: list[DrawRow] = []
    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for year in range(start, end + 1):
            html = fetch_year(client, year, cache_dir)
            rows = parse_year(html, year)
            print(f"[{year}] {len(rows):>4} 期")
            out.extend(rows)
            if not (cache_dir / f"year_{year}.html").exists():
                time.sleep(delay)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="爬取双色球历史开奖数据")
    ap.add_argument("--start", type=int, default=2003)
    ap.add_argument("--end", type=int, default=2026)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "_raw" / "years",
    )
    args = ap.parse_args()
    rows = scrape(args.start, args.end, args.out)
    print(f"总计 {len(rows)} 期")


if __name__ == "__main__":
    main()
