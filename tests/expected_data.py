"""全量数据集的实测基线 —— 所有「会随开奖更新而变化」的钉扎数字都集中在这里。

这些数字是**刻意**写死的：它们能立刻抓住数据被截断、漏采、错行的情况。
代价是每入库一期新号码都要刷新一次，所以集中在一个文件里，改完这一处即可。

刷新方法（在仓库根目录跑一次，把输出抄回本文件）::

    python - <<'PY'
    import collections, sys; sys.path.insert(0, ".")
    from shuangseqiu import dataset, crowding
    records = dataset.read_records("data/双色球历史开奖数据_全量.xlsx")
    report = crowding.compute_crowding(records)
    print("PERIODS      =", len(records), " # 最新", records[-1].issue)
    print("LATEST_ISSUE =", records[-1].issue)
    print("YEAR_COUNTS  =", dict(sorted(collections.Counter(r.year for r in records).items())))
    print("USABLE_PERIODS =", report.usable_periods)
    print("BASELINE_ACTUAL_WINNERS =", report.baseline.actual_winners)
    print("BASELINE_EXPECTED_WINNERS =", round(report.baseline.expected_winners))
    print("DISTINCT_RED_GROUPS =", len({frozenset(r.reds) for r in records}))
    latest = records[-1]
    print("LATEST_REDS   =", latest.reds)
    print("LATEST_BLUE   =", latest.blue)
    print("LATEST_BONUS  =", (latest.sales, latest.pool, latest.first_winners,
                              latest.first_prize, latest.second_winners, latest.second_prize))
    PY

下列内容**不属于**本文件，因为它们是代码性质的不变量、不该随数据变：

- 组合空间常量（``C(33,6)``、``17 721 088`` 等）；
- 拥挤指数的**结构性**结论（蓝球呈「倒 U + 9 吉利」双峰、红球含大号偏冷 / 全小号偏热、
  3 连号反偏热等），由 :mod:`test_crowding` 用 ``SHAPE_TOLERANCE = 0.005`` 守住。

关于那 0.005：拥挤指数的**逐格数值会随开奖漂移**，不是永久不变量。小组尤其明显——
新增一期就同时改动分子和分母，``high_red_high_blue`` 只覆盖几百期，
2026110 期（红球含 32、蓝球 14）恰好同时落进它和 ``blue[14]``，
两格分别从 0.746→0.7480、0.761→0.7635。容差取 0.005 是为了不每期都改数字，
同时仍能抓住真回归（公式坏了会偏出去远不止 0.005，且「冷 1.376 / 热 0.748」这类
跨号差异本身就有 0.6 量级）。

- 「每年自 001 起连续」「无缺失年份」「无越界号码」这类结构判定。
"""

from __future__ import annotations

# 2026-09-23 刷新：入库 2026110 期（02 05 16 19 26 32 +14，2026-09-22 开出）
PERIODS = 3507
LATEST_ISSUE = 2026110
FIRST_ISSUE = 2003001
YEARS: tuple[int, ...] = tuple(range(2003, 2027))

# 最新一期的号码与奖金栏。钉住它是因为「读取时有没有把奖金列并进来」只能
# 通过最新一期验出来——历史行早已被反复核过，新写入的行才是风险点。
LATEST_REDS = (2, 5, 16, 19, 26, 32)
LATEST_BLUE = 14
# (销售额, 奖池, 一等奖注数, 一等奖单注奖金, 二等奖注数, 二等奖单注奖金)
LATEST_BONUS = (327624764, 930967826, 12, 6654980, 173, 143495)

# 各年份期数的实测基线（2003–2004 年每周 2 期，2005 年起每周 3 期，2020 年因疫情减期）。
# 数据被替换、漏采或截断时，这条基线会立刻报警。
YEAR_COUNTS = {
    2003: 89,
    2004: 122,
    2005: 153,
    2006: 154,
    2007: 153,
    2008: 154,
    2009: 154,
    2010: 153,
    2011: 153,
    2012: 154,
    2013: 154,
    2014: 152,
    2015: 154,
    2016: 153,
    2017: 154,
    2018: 153,
    2019: 151,
    2020: 134,
    2021: 150,
    2022: 150,
    2023: 151,
    2024: 151,
    2025: 151,
    2026: 110,
}

# 拥挤指数的可用期数：2003 年整整 89 期销售额为 0，既不进分子也不进分母。
# 这两个数一起钉住，是为了守住「has_bonus 过滤没被跳过」——
# 漏过滤时基线数值碰巧几乎不变，但可用期数会立刻露馅。
PERIODS_WITHOUT_SALES = 89
USABLE_PERIODS = PERIODS - PERIODS_WITHOUT_SALES

# 基线自检：实际一等奖注数应当非常接近理论注数，说明「实际 ÷ 理论」的公式自洽。
BASELINE_ACTUAL_WINNERS = 28237
BASELINE_EXPECTED_WINNERS = 28526

# 红球组合去重：期数 − 组数 = 完全重复的对数。
# 生日问题的期望是 n(n-1)/(2N)（N = C(33,6) = 1 107 568），实测始终落在随机涨落内。
DISTINCT_RED_GROUPS = 3501
DUPLICATE_RED_PAIRS = PERIODS - DISTINCT_RED_GROUPS
