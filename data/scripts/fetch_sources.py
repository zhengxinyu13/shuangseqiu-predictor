"""多源抓取双色球开奖数据（含本地缓存），用于交叉校验。

四个来源各司其职：

============  ==========================================  ==================
来源          说明                                        角色
============  ==========================================  ==================
55128.cn      用户指定的站点，分年 ?year=YYYY 整年返回      用户请求源
17500.cn      乐彩网纯文本全量数据（期号/日期/号码/奖池）   主数据源
500.com       走势图历史接口（含销售额与中奖注数）           第二校验源
cwl.gov.cn    中国福利彩票官方开奖公告 API，仅 2013+        权威仲裁源
============  ==========================================  ==================

用法::

    python fetch_sources.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "_raw"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _client(referer: str) -> httpx.Client:
    """构造带桌面 UA 与 Referer 的客户端。"""
    return httpx.Client(
        headers={"User-Agent": UA, "Referer": referer},
        timeout=60.0,
        follow_redirects=True,
        verify=False,  # 部分彩票站点证书链不全
    )


def fetch_17500() -> pd.DataFrame:
    """乐彩网全量文本数据。

    每行格式::

        期号 日期 红1..红6 蓝 摇奖序红1..6 销售额 奖池 一等奖注数 一等奖单注
        二等奖注数 二等奖单注 三等奖注数 三等奖单注 ...

    Returns:
        含 期号/日期/红球列表/蓝球/销售额/奖池/一二等奖信息的 DataFrame。
    """
    cache = RAW / "ssq_17500.txt"
    if cache.exists():
        text = cache.read_text(encoding="gb2312", errors="ignore")
    else:
        with _client("https://www.17500.cn/") as c:
            r = c.get("https://www.17500.cn/getData/ssq.TXT")
            r.raise_for_status()
        cache.write_bytes(r.content)
        text = r.content.decode("gb2312", "ignore")

    recs = []
    for parts in (ln.split() for ln in text.splitlines()):
        if len(parts) < 21 or not parts[0].isdigit() or len(parts[0]) != 7:
            continue
        recs.append(
            {
                "期号": int(parts[0]),
                "日期": parts[1],
                "红球": [int(x) for x in parts[2:8]],
                "蓝球": int(parts[8]),
                "销售额": int(parts[15]),
                "奖池": int(parts[16]),
                "一等奖注数": int(parts[17]),
                "一等奖单注奖金": int(parts[18]),
                "二等奖注数": int(parts[19]),
                "二等奖单注奖金": int(parts[20]),
            }
        )
    return pd.DataFrame(recs).sort_values("期号").reset_index(drop=True)


def fetch_500() -> pd.DataFrame:
    """500.com 走势图历史接口，返回 start~end 区间全部记录。"""
    cache = RAW / "500com_history.htm"
    if not cache.exists():
        with _client("https://datachart.500.com/ssq/history/history.shtml") as c:
            r = c.get(
                "https://datachart.500.com/ssq/history/newinc/history.php",
                params={"start": "03001", "end": "26108"},
            )
            r.raise_for_status()
        cache.write_text(r.content.decode("gb2312", "ignore"), encoding="utf-8")

    html = cache.read_text(encoding="utf-8")

    def cells(tr: str) -> list[str]:
        # 行首有 <!--<td>2</td>--> 注释列，会被正则一起匹配，故按内容长度过滤
        return [
            re.sub(r"<[^>]+>", "", x).replace("&nbsp;", "").strip()
            for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        ]

    recs = []
    for c in (cells(tr) for tr in re.findall(r'<tr class="t_tr1">(.*?)</tr>', html, re.S)):
        if len(c) < 17:
            continue
        recs.append(
            {
                "期号": int("20" + c[1]),
                "红球": [int(c[i]) for i in range(2, 8)],
                "蓝球": int(c[8]),
                "日期_500": c[16],
            }
        )
    return pd.DataFrame(recs).sort_values("期号").reset_index(drop=True)


def fetch_cwl_official() -> pd.DataFrame:
    """中国福利彩票官网开奖公告 API（覆盖 2013-01-01 起）。"""
    cache = RAW / "cwl_official.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        with _client("https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/") as c:
            r = c.get(
                "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice",
                params={
                    "name": "ssq", "issueStart": "", "issueEnd": "",
                    "issueCount": "", "dayStart": "", "dayEnd": "",
                    "pageNo": 1, "pageSize": 100,
                },
            )
            r.raise_for_status()
        data = r.json().get("result") or []
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    return pd.DataFrame(
        [
            {
                "期号": int(x["code"]),
                "红球": sorted(int(v) for v in x["red"].split(",")),
                "蓝球": int(x["blue"]),
                "日期_官方": x["date"][:10],
            }
            for x in data
        ]
    ).sort_values("期号").reset_index(drop=True)


def fetch_55128(start: int = 2003, end: int = 2026) -> pd.DataFrame:
    """用户指定的 55128.cn，按年抓取整年列表。"""
    from scrape_ssq import scrape

    rows = scrape(start, end, RAW / "years")
    recs = []
    for r in rows:
        recs.append(
            {
                "期号": int(r["issue"]),
                "日期_55128": r["date"],
                "红球": r["reds"],
                "蓝球": r["blue"],
            }
        )
    return pd.DataFrame(recs).sort_values("期号").reset_index(drop=True)


if __name__ == "__main__":
    for name, fn in [
        ("乐彩网 17500.cn", fetch_17500),
        ("500.com", fetch_500),
        ("官方 cwl.gov.cn", fetch_cwl_official),
        ("55128.cn", fetch_55128),
    ]:
        d = fn()
        print(f"{name:>18}: {len(d):>5} 期  {d['期号'].min()} ~ {d['期号'].max()}")
