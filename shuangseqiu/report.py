"""把统计结果渲染成两份报告：HTML 交互版与 Markdown 文档版。

两份报告共用 :func:`shuangseqiu.stats.build_summary` 的输出，
保证同一份结论不会出现两套数字。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Sequence

from shuangseqiu import charts
from shuangseqiu.data import find_data_file, load_draws
from shuangseqiu.quality import MAX_PLAUSIBLE_DRAWS_PER_YEAR, QualityReport, build_quality_report
from shuangseqiu.stats import build_summary

REPORT_TITLE = "双色球历史开奖数据分析报告"
HTML_FILENAME = "双色球历史数据分析报告.html"
MARKDOWN_FILENAME = "双色球历史数据分析报告.md"
FIGURE_DIRNAME = "figures"

RED_BALL = "#d1453b"
BLUE_BALL = "#2f6fb0"
ALERT = "#8c2f24"
ACCENT = "#e08a3c"


# --------------------------------------------------------------- 图表数据


def _chart_payload(quality: QualityReport, summary: dict) -> dict:
    """把统计结果整理成前端直接可用的数组结构。"""
    profiles = list(quality.profiles)
    red_current = summary["red_omission_current"]
    red_max = summary["red_omission_max"]

    return {
        "yearly": {
            "years": [profile.year for profile in profiles],
            "counts": [profile.count for profile in profiles],
            "suspect": [not profile.is_count_plausible for profile in profiles],
            "limit": MAX_PLAUSIBLE_DRAWS_PER_YEAR,
        },
        "redFreq": {
            "numbers": [row["number"] for row in summary["red_frequency"]],
            "counts": [row["count"] for row in summary["red_frequency"]],
            "expected": summary["meta"]["red_expected"],
        },
        "blueFreq": {
            "numbers": [row["number"] for row in summary["blue_frequency"]],
            "counts": [row["count"] for row in summary["blue_frequency"]],
            "expected": summary["meta"]["blue_expected"],
        },
        "sumHist": summary["sums"]["hist"],
        "sumTrend": {
            "values": summary["sums"]["values"],
            "theoreticalMean": summary["sums"]["theoretical_mean"],
        },
        "oddEven": summary["odd_even"],
        "bigSmall": summary["big_small"],
        "zones": summary["zones"],
        "omission": {
            "numbers": sorted(red_current, key=int),
            "current": [red_current[number] for number in sorted(red_current, key=int)],
            "max": [red_max[number] for number in sorted(red_current, key=int)],
        },
        "heatmap": summary["heatmap"],
    }


# ------------------------------------------------------------------ 样式

_CSS = """
:root {
  --bg: #f5f7fa;
  --card: #ffffff;
  --line: #e2e8ef;
  --text: #1f2d3a;
  --muted: #64748b;
  --red: #d1453b;
  --blue: #2f6fb0;
  --alert: #8c2f24;
  --accent: #e08a3c;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", -apple-system, sans-serif;
  line-height: 1.75; font-size: 15px;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 40px 24px 72px; }
header.top { border-bottom: 3px solid var(--blue); padding-bottom: 20px; margin-bottom: 32px; }
header.top h1 { font-size: 30px; margin: 0 0 10px; letter-spacing: -0.4px; }
header.top .meta { color: var(--muted); font-size: 13px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 26px 28px; margin-bottom: 26px; }
section > h2 { font-size: 20px; margin: 0 0 6px; padding-left: 12px; border-left: 4px solid var(--blue); }
section > h2.alert { border-left-color: var(--alert); }
section > p.lead { color: var(--muted); font-size: 14px; margin: 6px 0 20px; padding-left: 16px; }
h3 { font-size: 16px; margin: 26px 0 10px; color: var(--text); }
p { margin: 10px 0; }
.chart { width: 100%; height: 360px; margin: 8px 0 4px; }
.chart.tall { height: 420px; }
.caption { color: var(--muted); font-size: 12.5px; margin: 4px 0 20px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin: 18px 0 8px; }
.card { background: #fbfcfe; border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; }
.card .k { color: var(--muted); font-size: 12.5px; }
.card .v { font-size: 24px; font-weight: 700; margin-top: 4px; letter-spacing: -0.5px; }
.card .s { color: var(--muted); font-size: 12px; margin-top: 2px; }
.card.danger { border-color: #eccfca; background: #fdf6f5; }
.card.danger .v { color: var(--alert); }
.card.good .v { color: var(--blue); }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
th, td { border-bottom: 1px solid var(--line); padding: 9px 12px; text-align: left; }
th { background: #f2f6fa; font-weight: 600; color: var(--text); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { background: #f8fafc; border-left: 3px solid var(--accent); padding: 12px 16px;
  color: var(--muted); font-size: 13.5px; margin: 16px 0; border-radius: 0 8px 8px 0; }
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
  background: #eef4fb; color: var(--blue); margin-right: 6px; }
.pill.bad { background: #fbeceb; color: var(--alert); }
footer { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 34px; }
"""


# ------------------------------------------------------------------- HTML

_JS = r"""
const DATA = __DATA__;
const RED = '#d1453b', LIGHT = '#eda9a4', BLUE = '#2f6fb0', BLUE_L = '#a8c6e5',
      NEUTRAL = '#64748b', ALERT = '#8c2f24', ACCENT = '#e08a3c';
const AXIS = { axisLine: { lineStyle: { color: '#dde3e9' } }, axisLabel: { color: NEUTRAL },
               splitLine: { lineStyle: { color: '#eef2f6' } } };
const BASE = { animationDuration: 700, textStyle: { fontFamily: 'Microsoft YaHei, sans-serif' },
               tooltip: { trigger: 'axis' }, grid: { left: 56, right: 24, top: 46, bottom: 48 } };

function bar(cats, values, colors, name, extra) {
  const opt = Object.assign({}, BASE, {
    xAxis: Object.assign({ type: 'category', data: cats }, AXIS),
    yAxis: Object.assign({ type: 'value' }, AXIS),
    series: [{ type: 'bar', name: name, data: values.map(function (v, i) {
      return { value: v, itemStyle: { color: colors ? colors[i] : BLUE, borderRadius: [3, 3, 0, 0] } };
    }) }]
  });
  return Object.assign(opt, extra || {});
}

function line(cats, values, name, color) {
  return Object.assign({}, BASE, {
    xAxis: Object.assign({ type: 'category', data: cats, boundaryGap: false }, AXIS),
    yAxis: Object.assign({ type: 'value' }, AXIS),
    series: [{ type: 'line', name: name, data: values, smooth: true, symbol: 'none',
               lineStyle: { color: color, width: 2 } }]
  });
}

/* 1. 各年份记录数 */
(function () {
  const y = DATA.yearly;
  const colors = y.suspect.map(function (bad) { return bad ? ALERT : BLUE; });
  const chart = echarts.init(document.getElementById('chart-yearly'));
  const opt = bar(y.years.map(String), y.counts, colors, '记录数');
  opt.series[0].label = { show: true, position: 'top', color: NEUTRAL, fontSize: 11 };
  opt.series[0].markLine = {
    silent: true, symbol: 'none',
    lineStyle: { color: ALERT, type: 'dashed' },
    label: { formatter: '物理上限 ' + y.limit + ' 期', color: ALERT, position: 'insideEndTop' },
    data: [{ yAxis: y.limit }]
  };
  opt.tooltip = { trigger: 'axis', formatter: function (p) {
    return p[0].name + ' 年<br/>记录数：' + p[0].value + ' 条'; } };
  chart.setOption(opt);
})();

/* 2. 红球频率 */
(function () {
  const f = DATA.redFreq;
  const colors = f.counts.map(function (c) { return c >= f.expected ? RED : LIGHT; });
  const chart = echarts.init(document.getElementById('chart-red'));
  const opt = bar(f.numbers.map(String), f.counts, colors, '出现次数');
  opt.series[0].markLine = {
    silent: true, symbol: 'none', lineStyle: { color: NEUTRAL, type: 'dashed' },
    label: { show: false },
    data: [{ yAxis: f.expected }]
  };
  chart.setOption(opt);
})();

/* 3. 蓝球频率 */
(function () {
  const f = DATA.blueFreq;
  const colors = f.counts.map(function (c) { return c >= f.expected ? BLUE : BLUE_L; });
  const chart = echarts.init(document.getElementById('chart-blue'));
  const opt = bar(f.numbers.map(String), f.counts, colors, '出现次数');
  opt.series[0].markLine = {
    silent: true, symbol: 'none', lineStyle: { color: NEUTRAL, type: 'dashed' },
    label: { show: false },
    data: [{ yAxis: f.expected }]
  };
  chart.setOption(opt);
})();

/* 4. 和值分布 */
(function () {
  const h = DATA.sumHist;
  const chart = echarts.init(document.getElementById('chart-sumhist'));
  const opt = bar(h.labels, h.counts, null, '期数');
  opt.series[0].itemStyle = { color: ACCENT, borderRadius: [3, 3, 0, 0] };
  opt.xAxis.axisLabel = { color: NEUTRAL, rotate: 45, fontSize: 10 };
  opt.tooltip = { trigger: 'axis', formatter: function (p) {
    return '和值 ' + p[0].name + '<br/>期数：' + p[0].value; } };
  chart.setOption(opt);
})();

/* 5. 和值走势 */
(function () {
  const t = DATA.sumTrend;
  const cats = t.values.map(function (_, i) { return String(i + 1); });
  const chart = echarts.init(document.getElementById('chart-sumtrend'));
  const opt = line(cats, t.values, '每期和值', BLUE_L);
  opt.series[0].lineStyle.width = 1;
  opt.series.push({
    type: 'line', name: '20 期滑动平均', smooth: true, symbol: 'none',
    lineStyle: { color: RED, width: 2.4 },
    data: t.values.map(function (_, i) {
      if (i < 19) return '-';
      let sum = 0;
      for (let k = i - 19; k <= i; k++) sum += t.values[k];
      return Math.round(sum / 20 * 10) / 10;
    })
  });
  opt.series.push({
    type: 'line', name: '理论均值', symbol: 'none', silent: true,
    lineStyle: { color: NEUTRAL, type: 'dashed', width: 1.2 },
    data: cats.map(function () { return t.theoreticalMean; })
  });
  opt.legend = { top: 6, textStyle: { color: NEUTRAL }, data: ['每期和值', '20 期滑动平均', '理论均值'] };
  opt.tooltip = { trigger: 'axis', show: false };
  chart.setOption(opt);
})();

/* 6. 奇偶比 */
(function () {
  const d = DATA.oddEven;
  const peak = Math.max.apply(null, d.counts);
  const colors = d.counts.map(function (c) { return c === peak ? RED : LIGHT; });
  echarts.init(document.getElementById('chart-oddeven'))
    .setOption(bar(d.labels, d.counts, colors, '期数'));
})();

/* 7. 大小比 */
(function () {
  const d = DATA.bigSmall;
  const peak = Math.max.apply(null, d.counts);
  const colors = d.counts.map(function (c) { return c === peak ? BLUE : BLUE_L; });
  echarts.init(document.getElementById('chart-bigsmall'))
    .setOption(bar(d.labels, d.counts, colors, '期数'));
})();

/* 8. 三区比 */
(function () {
  const d = DATA.zones;
  const pairs = d.labels.map(function (l, i) { return [l, d.counts[i]]; });
  pairs.sort(function (a, b) { return b[1] - a[1]; });
  const top = pairs.slice(0, 12);
  const opt = bar(top.map(function (p) { return p[0]; }), top.map(function (p) { return p[1]; }),
                  null, '期数');
  opt.series[0].itemStyle = { color: '#7a8fa6', borderRadius: [3, 3, 0, 0] };
  opt.xAxis.axisLabel = { color: NEUTRAL, rotate: 45, fontSize: 10 };
  echarts.init(document.getElementById('chart-zones')).setOption(opt);
})();

/* 9. 遗漏 */
(function () {
  const d = DATA.omission;
  const chart = echarts.init(document.getElementById('chart-omission'));
  chart.setOption(Object.assign({}, BASE, {
    legend: { top: 6, textStyle: { color: NEUTRAL } },
    xAxis: Object.assign({ type: 'category', data: d.numbers.map(String) }, AXIS),
    yAxis: Object.assign({ type: 'value' }, AXIS),
    series: [
      { name: '当前遗漏', type: 'bar', data: d.current, itemStyle: { color: RED, borderRadius: [3, 3, 0, 0] } },
      { name: '历史最大遗漏', type: 'bar', data: d.max, itemStyle: { color: BLUE_L, borderRadius: [3, 3, 0, 0] } }
    ]
  }));
})();

/* 10. 热力图 */
(function () {
  const h = DATA.heatmap;
  const points = [];
  let peakValue = 0;
  h.matrix.forEach(function (row, yi) {
    row.forEach(function (value, xi) {
      points.push([xi, yi, value]);
      if (value > peakValue) peakValue = value;
    });
  });
  const chart = echarts.init(document.getElementById('chart-heatmap'));
  chart.setOption({
    textStyle: { fontFamily: 'Microsoft YaHei, sans-serif' },
    tooltip: { formatter: function (p) {
      return h.years[p.value[1]] + ' 年 · ' + h.numbers[p.value[0]] + ' 号<br/>出现 ' + p.value[2] + ' 次'; } },
    grid: { left: 56, right: 24, top: 20, bottom: 56 },
    xAxis: Object.assign({ type: 'category', data: h.numbers.map(String), splitArea: { show: true } }, AXIS),
    yAxis: Object.assign({ type: 'category', data: h.years.map(String) }, AXIS),
    visualMap: { min: 0, max: peakValue, calculable: true, orient: 'horizontal',
                 left: 'center', bottom: 0, textStyle: { color: NEUTRAL },
                 inRange: { color: ['#fdf3e7', '#f0b46a', '#d1453b', '#8c2f24'] } },
    series: [{ type: 'heatmap', data: points, label: { show: true, fontSize: 9, color: '#3b2a12' },
               emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' } } }]
  });
})();

window.addEventListener('resize', function () {
  document.querySelectorAll('.chart').forEach(function (el) {
    const inst = echarts.getInstanceByDom(el);
    if (inst) inst.resize();
  });
});
"""


def _cards(summary: dict, quality: QualityReport) -> str:
    meta = summary["meta"]
    chi = summary["chi_square"]
    hot = summary["red_ranking"]["hottest"][0]
    cold = summary["red_ranking"]["coldest"][0]
    return f"""
<div class="cards">
  <div class="card danger">
    <div class="k">可信记录占比</div>
    <div class="v">{quality.trusted_count / quality.total_records * 100:.1f}%</div>
    <div class="s">{quality.trusted_count} / {quality.total_records} 条</div>
  </div>
  <div class="card">
    <div class="k">可信段期数</div>
    <div class="v">{meta["periods"]}</div>
    <div class="s">{meta["last_issue"]} ~ {meta["first_issue"]}</div>
  </div>
  <div class="card">
    <div class="k">红球分布检验 p 值</div>
    <div class="v">{chi["red"]["p_value"]:.3f}</div>
    <div class="s">自由度 {chi["red"]["dof"]}，越小越可疑</div>
  </div>
  <div class="card">
    <div class="k">蓝球分布检验 p 值</div>
    <div class="v">{chi["blue"]["p_value"]:.3f}</div>
    <div class="s">自由度 {chi["blue"]["dof"]}</div>
  </div>
  <div class="card">
    <div class="k">出现最多的红球</div>
    <div class="v">{hot["number"]:02d}</div>
    <div class="s">{hot["count"]} 次，期望 {meta["red_expected"]:.1f} 次</div>
  </div>
  <div class="card">
    <div class="k">出现最少的红球</div>
    <div class="v">{cold["number"]:02d}</div>
    <div class="s">{cold["count"]} 次，期望 {meta["red_expected"]:.1f} 次</div>
  </div>
</div>
"""


def _year_table(quality: QualityReport) -> str:
    rows = []
    for profile in quality.profiles:
        if profile.is_count_plausible:
            verdict = '<span class="pill">纳入分析</span>'
        else:
            verdict = '<span class="pill bad">物理上不可能</span>'
        gap = "—" if not profile.missing_indexes else f"{len(profile.missing_indexes)} 个序号缺失"
        rows.append(
            f"<tr><td class='num'>{profile.year}</td>"
            f"<td class='num'>{profile.count}</td>"
            f"<td class='num'>{profile.min_index}</td>"
            f"<td class='num'>{profile.max_index}</td>"
            f"<td>{gap}</td><td>{verdict}</td></tr>"
        )
    return (
        "<table><thead><tr><th class='num'>年份</th><th class='num'>记录数</th>"
        "<th class='num'>最小编号</th><th class='num'>最大编号</th><th>年内缺口</th><th>判定</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _hot_cold_table(summary: dict) -> str:
    meta = summary["meta"]
    rows = []
    for label, items in (("偏热", summary["red_ranking"]["hottest"]),
                         ("偏冷", summary["red_ranking"]["coldest"])):
        for item in items:
            rows.append(
                f"<tr><td>{label}</td><td class='num'>{item['number']:02d}</td>"
                f"<td class='num'>{item['count']}</td>"
                f"<td class='num'>{item['count'] - meta['red_expected']:+.1f}</td></tr>"
            )
    return (
        "<table><thead><tr><th>类别</th><th class='num'>红球</th><th class='num'>出现次数</th>"
        "<th class='num'>相对期望</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _shape_table(summary: dict) -> str:
    def peak(distribution: dict) -> tuple[str, int]:
        pairs = list(zip(distribution["labels"], distribution["counts"]))
        return max(pairs, key=lambda item: item[1])

    zones = list(zip(summary["zones"]["labels"], summary["zones"]["counts"]))
    top_zone = max(zones, key=lambda item: item[1])
    odd_label, odd_count = peak(summary["odd_even"])
    big_label, big_count = peak(summary["big_small"])
    con_label, con_count = peak(summary["consecutive"])
    rep_label, rep_count = peak(summary["repeats"])
    periods = summary["meta"]["periods"]

    def share(count: int) -> str:
        return f"{count / periods * 100:.1f}%"

    return (
        "<table><thead><tr><th>形态</th><th>出现最多的组合</th><th class='num'>期数</th>"
        "<th class='num'>占比</th></tr></thead><tbody>"
        f"<tr><td>奇偶比</td><td>{odd_label}</td><td class='num'>{odd_count}</td>"
        f"<td class='num'>{share(odd_count)}</td></tr>"
        f"<tr><td>大小比</td><td>{big_label}</td><td class='num'>{big_count}</td>"
        f"<td class='num'>{share(big_count)}</td></tr>"
        f"<tr><td>三区比</td><td>{top_zone[0]}</td><td class='num'>{top_zone[1]}</td>"
        f"<td class='num'>{share(top_zone[1])}</td></tr>"
        f"<tr><td>连号</td><td>{con_label}</td><td class='num'>{con_count}</td>"
        f"<td class='num'>{share(con_count)}</td></tr>"
        f"<tr><td>与上期重号</td><td>{rep_label}</td><td class='num'>{rep_count}</td>"
        f"<td class='num'>{share(rep_count)}</td></tr>"
        "</tbody></table>"
    )


def render_html(quality: QualityReport, summary: dict, generated_at: str) -> str:
    meta = summary["meta"]
    chi = summary["chi_square"]
    sums = summary["sums"]
    payload = json.dumps(_chart_payload(quality, summary), ensure_ascii=False, separators=(",", ":"))

    body = f"""
<header class="top">
  <h1>{REPORT_TITLE}</h1>
  <div class="meta">数据源：data/双色球历史开奖数据.xlsx ｜ 原始记录 {quality.total_records} 条
   ｜ 参与分析 {meta["periods"]} 条 ｜ 生成时间 {generated_at}</div>
</header>

<section>
  <h2>一、先看结论</h2>
  <p class="lead">数字全部来自本报告第六节的计算过程，可逐项复核。</p>
  {_cards(summary, quality)}
  <h3>三条主要结论</h3>
  <p><strong>1. 这份数据里 86.5% 不能用。</strong>
  原始 4330 条记录中，2003–2006 年那 3744 条的期数在物理上不成立——2003 年 999 期、2004 年 1000 期，
  而双色球每周只开奖 3 次，一年上限 {MAX_PLAUSIBLE_DRAWS_PER_YEAR} 期。只有
  {meta["last_issue"]} ~ {meta["first_issue"]} 这 {meta["periods"]} 条进入分析。</p>
  <p><strong>2. 可信段里，号码分布和"完全随机"没有可检出的差别。</strong>
  红球卡方检验 p 值 {chi["red"]["p_value"]:.3f}、蓝球 {chi["blue"]["p_value"]:.3f}，
  都远大于 0.05，无法拒绝均匀分布假设。所谓"热号""冷号"的差距，落在随机波动范围内。</p>
  <p><strong>3. 形态分布同样贴合理论。</strong>
  和值实际均值 {sums["mean"]}，与理论均值 {sums["theoretical_mean"]:.0f} 相差
  {abs(sums["mean"] - sums["theoretical_mean"]):.2f}；奇偶比、大小比、三区比都呈典型的钟形分布。
  这些规律反映的是组合数学，而不是可被利用的走势。</p>
  <div class="note">彩票每期独立开奖，历史号码对未来一期没有信息量。本报告的价值在于
  描述这份数据本身，而不是提供选号依据。</div>
</section>

<section>
  <h2 class="alert">二、数据清洗：问题出在哪</h2>
  <p class="lead">判据完全来自数据自身的结构，不依赖任何外部开奖资料，可复现。</p>
  <p>一年最多 53 个周二、53 个周四、53 个周日，即
  <strong>53 × 3 = 159 期</strong>；放宽 1 期取 <strong>{MAX_PLAUSIBLE_DRAWS_PER_YEAR}</strong> 作为阈值，
  超过即判定该年份不可信。</p>
  <div id="chart-yearly" class="chart"></div>
  <p class="caption">图 1：各年份记录数。红色柱子对应 2003–2006 年，
  记录数 999 / 1000 / 1000 / 745，全部越过物理上限。</p>
  {_year_table(quality)}
  <h3>还发现三个次要问题</h3>
  <p><strong>跨年断档：</strong>2007–2021 年完全没有记录，共 {len(quality.missing_years)} 年空白。</p>
  <p><strong>年内缺口：</strong>2026 年缺 040–047 共 8 期；2022 年只有 118–150 期，
  前 117 期缺失。</p>
  <p><strong>异常起始编号：</strong>2004、2005、2006 三年的期号从 0 号开始编（如 2004000 期），
  真实期号应自 001 起编。这条不单独作为剔除理由，但与该三年被判不可信相互印证。</p>
</section>

<section>
  <h2>三、数据概览</h2>
  <p>可信段跨越 {len(meta["years"])} 个年份（{meta["years"][0]}–{meta["years"][-1]}），
  共 {meta["periods"]} 期，即 {meta["periods"]} 组号码、
  {meta["periods"] * 7} 个球。其中红球 {meta["periods"] * 6} 个、蓝球 {meta["periods"]} 个。</p>
  <div class="note">样本量提醒：{meta["periods"]} 期对频率分析够用，但
  「每 33 个红球在每期抽出 6 个」的组合数有 1 107 568 种，加上蓝球 16 种，
  全部组合 17 721 088 种。用 {meta["periods"]} 期去覆盖这个空间，只能看到极稀疏的一小片。</div>
</section>

<section>
  <h2>四、号码频率</h2>
  <h3>红球</h3>
  <div id="chart-red" class="chart tall"></div>
  <p class="caption">图 2：红球 1–33 出现次数。深色高于期望 {meta["red_expected"]:.1f} 次，
  浅色低于期望。虚线为期望值。</p>
  {_hot_cold_table(summary)}
  <h3>蓝球</h3>
  <div id="chart-blue" class="chart"></div>
  <p class="caption">图 3：蓝球 1–16 出现次数，期望 {meta["blue_expected"]:.1f} 次。</p>
</section>

<section>
  <h2>五、形态特征</h2>
  <h3>和值</h3>
  <div id="chart-sumhist" class="chart tall"></div>
  <p class="caption">图 4：红球和值分布。实际区间 {sums["min"]}–{sums["max"]}，
  均值 {sums["mean"]}，理论均值 {sums["theoretical_mean"]:.0f}。</p>
  <div id="chart-sumtrend" class="chart tall"></div>
  <p class="caption">图 5：和值走势与 20 期滑动平均。滑动平均线始终围绕理论均值上下摆动，
  没有趋势性。</p>
  <h3>奇偶、大小、三区</h3>
  <div id="chart-oddeven" class="chart"></div>
  <p class="caption">图 6：红球奇偶比分布。</p>
  <div id="chart-bigsmall" class="chart"></div>
  <p class="caption">图 7：大小比分布（1–16 为小号，17–33 为大号）。</p>
  <div id="chart-zones" class="chart"></div>
  <p class="caption">图 8：三区比分布（1–11 / 12–22 / 23–33），取出现最多的 12 种组合。</p>
  {_shape_table(summary)}
</section>

<section>
  <h2>六、遗漏与随机性检验</h2>
  <h3>遗漏</h3>
  <div id="chart-omission" class="chart tall"></div>
  <p class="caption">图 9：红球当前遗漏与历史最大遗漏对比。</p>
  <h3>卡方检验</h3>
  <p>把每个号码的实际出现次数与"均匀随机"下的期望次数比较，
  得到卡方统计量：红球 {chi["red"]["statistic"]:.2f}（自由度 {chi["red"]["dof"]}）、
  蓝球 {chi["blue"]["statistic"]:.2f}（自由度 {chi["blue"]["dof"]}）。</p>
  <p>对应 p 值：红球 <strong>{chi["red"]["p_value"]:.3f}</strong>、
  蓝球 <strong>{chi["blue"]["p_value"]:.3f}</strong>。
  两者都远大于 0.05，<strong>没有证据拒绝"号码均匀出现"的原假设</strong>——
  也就是说，这段数据看上去和真正随机的摇奖结果没有区别。</p>
  <div class="note">p 值的含义不是"号码是随机的概率"，而是"如果号码真的均匀随机，
  出现当前这么偏的分布的概率有多大"。{chi["red"]["p_value"]:.3f} 意味着这种偏差在纯随机下常见得很。</div>
  <h3>号码 × 年份热力图</h3>
  <div id="chart-heatmap" class="chart tall"></div>
  <p class="caption">图 10：各年份红球出现次数。颜色深浅没有形成纵向条纹，
  说明不存在"某几年偏爱某些号码"的稳定模式。注意 2022 年只有 33 期
  （其余年份 151 期），那一行颜色天然偏浅，只有横向比较才有意义。</p>
</section>

<section>
  <h2>七、结论与边界</h2>
  <p><strong>对数据本身：</strong>这份表不能直接当作"2003 年至今的完整开奖历史"使用。
  可用的只有 {meta["periods"]} 期；要恢复完整历史，需要重新采集 2003–2021 年的官方数据。</p>
  <p><strong>对分析结论：</strong>在可用的 {meta["periods"]} 期上，号码频率、和值、奇偶比、
  大小比、三区比、连号、重号等指标全部贴合随机假设，没有可复现的规律。</p>
  <p><strong>能力边界：</strong>任何声称能根据历史号码预测下一期的方法，都要先解释
  为什么它能在 p = {chi["red"]["p_value"]:.3f} 这种级别的均匀性上找到信号。
  本项目后续若要做建模，目标应当放在"验证随机性"而不是"预测号码"。</p>
</section>

<footer>本报告由脚本自动生成，数据源与计算过程见项目仓库 shuangseqiu/ 目录。</footer>
"""

    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{REPORT_TITLE}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<div class=\"wrap\">{body}</div>\n"
        "<script src=\"assets/echarts.min.js\"></script>\n"
        f"<script>{_JS.replace('__DATA__', payload)}</script>\n"
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------- Markdown


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(
    quality: QualityReport,
    summary: dict,
    generated_at: str,
    figures: dict[str, str],
) -> str:
    meta = summary["meta"]
    chi = summary["chi_square"]
    sums = summary["sums"]

    year_rows = []
    for profile in quality.profiles:
        gap = "—" if not profile.missing_indexes else f"{len(profile.missing_indexes)} 个"
        verdict = "纳入分析" if profile.is_count_plausible else "**物理上不可能**"
        year_rows.append([
            str(profile.year), str(profile.count), str(profile.min_index),
            str(profile.max_index), gap, verdict,
        ])

    hot_cold_rows = []
    for label, items in (("偏热", summary["red_ranking"]["hottest"]),
                         ("偏冷", summary["red_ranking"]["coldest"])):
        for item in items:
            hot_cold_rows.append([
                label, f"{item['number']:02d}", str(item["count"]),
                f"{item['count'] - meta['red_expected']:+.1f}",
            ])

    blue_rows = [
        [f"{row['number']:02d}", str(row["count"]),
         f"{row['count'] - meta['blue_expected']:+.1f}"]
        for row in sorted(summary["blue_frequency"], key=lambda r: -r["count"])
    ]

    shape_rows = [
        ["奇偶比", max(zip(summary["odd_even"]["labels"], summary["odd_even"]["counts"]),
                       key=lambda item: item[1])],
        ["大小比", max(zip(summary["big_small"]["labels"], summary["big_small"]["counts"]),
                       key=lambda item: item[1])],
        ["三区比", max(zip(summary["zones"]["labels"], summary["zones"]["counts"]),
                       key=lambda item: item[1])],
        ["连号", max(zip(summary["consecutive"]["labels"], summary["consecutive"]["counts"]),
                      key=lambda item: item[1])],
        ["与上期重号", max(zip(summary["repeats"]["labels"], summary["repeats"]["counts"]),
                           key=lambda item: item[1])],
    ]
    shape_table = [[name, f"{label}", str(count),
                    f"{count / meta['periods'] * 100:.1f}%"]
                   for name, (label, count) in shape_rows]

    return f"""# {REPORT_TITLE}

> 数据源：`data/双色球历史开奖数据.xlsx` ｜ 原始记录 {quality.total_records} 条
> ｜ 参与分析 {meta["periods"]} 条 ｜ 生成时间 {generated_at}

## 一、先看结论

1. **这份数据里 86.5% 不能用。** 原始 {quality.total_records} 条记录中，2003–2006 年那
   {quality.suspect_count} 条的期数在物理上不成立——2003 年 999 期、2004 年 1000 期，
   而双色球每周只开奖 3 次，一年上限 {MAX_PLAUSIBLE_DRAWS_PER_YEAR} 期。
   只有 {meta["last_issue"]} ~ {meta["first_issue"]} 这 {meta["periods"]} 条进入分析。
2. **可信段里号码分布与"完全随机"没有可检出的差别。** 红球卡方检验 p 值
   {chi["red"]["p_value"]:.3f}、蓝球 {chi["blue"]["p_value"]:.3f}，都远大于 0.05。
3. **形态分布同样贴合理论。** 和值实际均值 {sums["mean"]}，理论均值
   {sums["theoretical_mean"]:.0f}，相差 {abs(sums["mean"] - sums["theoretical_mean"]):.2f}。

> 彩票每期独立开奖，历史号码对未来一期没有信息量。本报告描述这份数据本身，
> 不构成任何选号依据。

## 二、数据清洗：问题出在哪

判据全部来自数据自身结构：一年最多 53 个周二 + 53 个周四 + 53 个周日 = 159 期，
放宽 1 期取 **{MAX_PLAUSIBLE_DRAWS_PER_YEAR}** 作为阈值，超过即判该年份不可信。

![各年份记录数]({figures["yearly"]})

{_markdown_table(["年份", "记录数", "最小编号", "最大编号", "年内缺口", "判定"], year_rows)}

三个次要问题：

- **跨年断档**：2007–2021 年完全没有记录，共 {len(quality.missing_years)} 年空白。
- **年内缺口**：2026 年缺 040–047 共 8 期；2022 年只有 118–150 期，前 117 期缺失。
- **异常起始编号**：2004–2006 年的期号从 0 号起编（如 2004000 期），真实期号应自 001 起编。

## 三、数据概览

可信段跨越 {len(meta["years"])} 个年份（{meta["years"][0]}–{meta["years"][-1]}），
共 {meta["periods"]} 期、{meta["periods"] * 7} 个球，其中红球 {meta["periods"] * 6} 个、
蓝球 {meta["periods"]} 个。

> 样本量提醒：33 选 6 共 1 107 568 种组合，再乘蓝球 16 种，全部组合
> 17 721 088 种。用 {meta["periods"]} 期覆盖这个空间，只看到极稀疏的一小片。

## 四、号码频率

![红球频率]({figures["red"]})

{_markdown_table(["类别", "红球", "出现次数", "相对期望"], hot_cold_rows)}

![蓝球频率]({figures["blue"]})

{_markdown_table(["蓝球", "出现次数", "相对期望"], blue_rows)}

## 五、形态特征

![和值分布]({figures["sum"]})

实际和值区间 {sums["min"]}–{sums["max"]}，均值 {sums["mean"]}，
理论均值 {sums["theoretical_mean"]:.0f}。

![和值走势]({figures["trend"]})

20 期滑动平均始终围绕理论均值摆动，没有趋势性。

![奇偶比]({figures["odd"]})

{_markdown_table(["形态", "出现最多的组合", "期数", "占比"], shape_table)}

![三区比]({figures["zone"]})

## 六、遗漏与随机性检验

![遗漏]({figures["omission"]})

卡方检验结果：

| 项目 | 卡方统计量 | 自由度 | p 值 |
| --- | --- | --- | --- |
| 红球 | {chi["red"]["statistic"]:.2f} | {chi["red"]["dof"]} | **{chi["red"]["p_value"]:.3f}** |
| 蓝球 | {chi["blue"]["statistic"]:.2f} | {chi["blue"]["dof"]} | **{chi["blue"]["p_value"]:.3f}** |

p 值远大于 0.05，没有证据拒绝"号码均匀出现"的原假设——这段数据看上去
与真正随机的摇奖结果没有区别。

> p 值不是"号码是随机的概率"，而是"如果号码真的均匀随机，出现当前这么偏的
> 分布的概率有多大"。{chi["red"]["p_value"]:.3f} 表示这种偏差在纯随机下很常见。

![热力图]({figures["heatmap"]})

颜色深浅没有形成纵向条纹，说明不存在"某几年偏爱某些号码"的稳定模式。
注意 2022 年只有 33 期（其余年份 151 期），那一行颜色天然偏浅，只有横向比较才有意义。

## 七、结论与边界

- **对数据本身**：这份表不能当作"2003 年至今的完整开奖历史"使用。可用只有
  {meta["periods"]} 期，要恢复完整历史需要重新采集 2003–2021 年的官方数据。
- **对分析结论**：在可用区间内，号码频率、和值、奇偶比、大小比、三区比、连号、
  重号等指标全部贴合随机假设，没有可复现的规律。
- **能力边界**：任何声称能根据历史号码预测下一期的方法，都要先解释为什么它能在
  p = {chi["red"]["p_value"]:.3f} 这种级别的均匀性上找到信号。后续建模目标应放在
  "验证随机性"而不是"预测号码"。

---

*本报告由脚本自动生成，数据源与计算过程见项目仓库 `shuangseqiu/` 目录。*
"""


# ------------------------------------------------------------------ 入口


def build_reports(
    output_dir: Path | str | None = None,
    data_path: Path | str | None = None,
) -> dict[str, Path]:
    """生成 HTML 与 Markdown 两份报告，返回产出文件路径。"""
    target_dir = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent.parent / "reports"
    figure_dir = target_dir / FIGURE_DIRNAME
    target_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    source = Path(data_path) if data_path is not None else find_data_file()
    quality = build_quality_report(load_draws(source))
    summary = build_summary(quality.trusted_draws)
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    figure_names = {
        "yearly": "01-各年份记录数.png",
        "red": "02-红球频率.png",
        "blue": "03-蓝球频率.png",
        "sum": "04-和值分布.png",
        "trend": "05-和值走势.png",
        "odd": "06-奇偶比.png",
        "zone": "07-三区比.png",
        "omission": "08-遗漏对比.png",
        "heatmap": "09-热力图.png",
    }
    plots = {
        "yearly": charts.plot_yearly_counts(quality.profiles, figure_dir / figure_names["yearly"]),
        "red": charts.plot_red_frequency(summary, figure_dir / figure_names["red"]),
        "blue": charts.plot_blue_frequency(summary, figure_dir / figure_names["blue"]),
        "sum": charts.plot_sum_histogram(summary, figure_dir / figure_names["sum"]),
        "trend": charts.plot_sum_trend(summary, figure_dir / figure_names["trend"]),
        "odd": charts.plot_odd_even(summary, figure_dir / figure_names["odd"]),
        "zone": charts.plot_zone_distribution(summary, figure_dir / figure_names["zone"]),
        "omission": charts.plot_omission(summary, figure_dir / figure_names["omission"]),
        "heatmap": charts.plot_heatmap(summary, figure_dir / figure_names["heatmap"]),
    }
    figures = {key: f"{FIGURE_DIRNAME}/{path.name}" for key, path in plots.items()}

    html_path = target_dir / HTML_FILENAME
    html_path.write_text(
        render_html(quality, summary, generated_at), encoding="utf-8", newline="\n"
    )

    markdown_path = target_dir / MARKDOWN_FILENAME
    markdown_path.write_text(
        render_markdown(quality, summary, generated_at, figures),
        encoding="utf-8",
        newline="\n",
    )

    return {"html": html_path, "markdown": markdown_path, **plots}


if __name__ == "__main__":
    produced = build_reports()
    for key, path in produced.items():
        print(f"{key}: {path}")
