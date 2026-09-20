"""用 matplotlib 生成 Markdown 报告所需的静态图表（PNG）。

只负责画图，不做任何统计计算 —— 数据全部来自 :mod:`shuangseqiu.stats` 的结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")  # 无界面后端，服务器/脚本环境可用

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from shuangseqiu.quality import MAX_PLAUSIBLE_DRAWS_PER_YEAR, YearProfile  # noqa: E402

# ---------------------------------------------------------------- 主题配色

RED_BALL = "#d1453b"
RED_BALL_LIGHT = "#eda9a4"
BLUE_BALL = "#2f6fb0"
BLUE_BALL_LIGHT = "#a8c6e5"
NEUTRAL = "#5b6b7c"
ALERT = "#8c2f24"
GRID = "#dde3e9"
TITLE_COLOR = "#1f2d3a"

_CJK_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
)


def configure_chinese_font() -> str:
    """挑选一个可用的中文字体，否则图上中文会变成方块。"""
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return ""


ACTIVE_FONT = configure_chinese_font()


def _save(fig, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _style(ax, title: str, xlabel: str = "", ylabel: str = "", grid_axis: str = "y") -> None:
    ax.set_title(title, fontsize=13, fontweight="bold", color=TITLE_COLOR, pad=12)
    ax.set_xlabel(xlabel, fontsize=10, color=NEUTRAL)
    ax.set_ylabel(ylabel, fontsize=10, color=NEUTRAL)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=NEUTRAL, labelsize=9)


# ------------------------------------------------------------------- 图表


def plot_yearly_counts(profiles: Sequence[YearProfile], out_path: Path) -> Path:
    """各年份记录数，超过物理上限的年份标红。"""
    years = [str(profile.year) for profile in profiles]
    counts = [profile.count for profile in profiles]
    colors = [ALERT if not profile.is_count_plausible else BLUE_BALL for profile in profiles]
    has_suspect = any(not profile.is_count_plausible for profile in profiles)

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
    bars = ax.bar(years, counts, color=colors, width=0.62)
    for bar, count in zip(bars, counts):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(counts) * 0.02,
            str(count),
            ha="center",
            va="bottom",
            fontsize=9,
            color=NEUTRAL,
        )

    ax.axhline(
        MAX_PLAUSIBLE_DRAWS_PER_YEAR,
        color=ALERT,
        linestyle="--",
        linewidth=1.2,
    )
    ax.text(
        0.70,
        0.52,
        f"虚线 = 一年物理上限 {MAX_PLAUSIBLE_DRAWS_PER_YEAR} 期",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=9.5,
        color=ALERT,
    )
    # 判定文字随数据走：有越限年份才提"红色"，否则明确说明全部通过
    verdict = "红色 = 超过物理上限，不可信" if has_suspect else "全部年份均未超过上限"
    _style(ax, f"各年份记录数（{verdict}）", "年份", "记录数")
    return _save(fig, out_path)


def plot_red_frequency(summary: dict, out_path: Path) -> Path:
    """红球出现次数分布，并标出理论期望次数。"""
    rows = summary["red_frequency"]
    numbers = [str(row["number"]) for row in rows]
    counts = [row["count"] for row in rows]
    expected = summary["meta"]["red_expected"]
    colors = [RED_BALL if count >= expected else RED_BALL_LIGHT for count in counts]

    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    ax.bar(numbers, counts, color=colors, width=0.68)
    ax.axhline(expected, color=NEUTRAL, linestyle="--", linewidth=1.1)
    ax.text(
        0.985,
        0.96,
        f"虚线 = 理论期望 {expected:.1f} 次",
        transform=ax.transAxes,
        ha="right",
        va="center",
        fontsize=9.5,
        color=NEUTRAL,
    )
    _style(ax, "红球 1-33 出现次数", "红球号码", "出现次数")
    return _save(fig, out_path)


def plot_blue_frequency(summary: dict, out_path: Path) -> Path:
    """蓝球出现次数分布。"""
    rows = summary["blue_frequency"]
    numbers = [str(row["number"]) for row in rows]
    counts = [row["count"] for row in rows]
    expected = summary["meta"]["blue_expected"]
    colors = [BLUE_BALL if count >= expected else BLUE_BALL_LIGHT for count in counts]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    ax.bar(numbers, counts, color=colors, width=0.6)
    ax.axhline(expected, color=NEUTRAL, linestyle="--", linewidth=1.1)
    ax.text(
        0.985,
        0.96,
        f"虚线 = 理论期望 {expected:.1f} 次",
        transform=ax.transAxes,
        ha="right",
        va="center",
        fontsize=9.5,
        color=NEUTRAL,
    )
    _style(ax, "蓝球 1-16 出现次数", "蓝球号码", "出现次数")
    return _save(fig, out_path)


def plot_sum_histogram(summary: dict, out_path: Path) -> Path:
    """红球和值分布。"""
    hist = summary["sums"]["hist"]
    mean = summary["sums"]["mean"]
    # 把连续的和值均值映射到分桶的类别索引上
    mean_bucket = int((mean - 20) // 10)

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
    ax.bar(hist["labels"], hist["counts"], color="#e08a3c", width=0.72)
    ax.axvline(mean_bucket, color=ALERT, linestyle="--", linewidth=1.2)
    ax.text(
        mean_bucket,
        max(hist["counts"]) * 0.95,
        f"实际均值 {mean:.1f}",
        ha="center",
        va="top",
        fontsize=9,
        color=ALERT,
    )
    _style(ax, "红球和值分布", "和值区间", "期数")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    return _save(fig, out_path)


def plot_zone_distribution(summary: dict, out_path: Path) -> Path:
    """三区比分布（取出现最多的前 10 种组合）。"""
    labels = summary["zones"]["labels"]
    counts = summary["zones"]["counts"]
    paired = sorted(zip(labels, counts), key=lambda item: -item[1])[:10]
    top_labels = [item[0] for item in paired][::-1]
    top_counts = [item[1] for item in paired][::-1]

    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150)
    ax.barh(top_labels, top_counts, color="#7a8fa6", height=0.66)
    _style(
        ax,
        "三区比分布（出现最多的 10 种组合）",
        "期数",
        "三区比 1-11 / 12-22 / 23-33",
        grid_axis="x",
    )
    return _save(fig, out_path)


def plot_omission(summary: dict, out_path: Path) -> Path:
    """红球当前遗漏与历史最大遗漏对比。"""
    current = summary["red_omission_current"]
    maximum = summary["red_omission_max"]
    numbers = [str(number) for number in sorted(current, key=int)]
    current_values = [current[int(number)] for number in numbers]
    max_values = [maximum[int(number)] for number in numbers]

    positions = np.arange(len(numbers))
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    ax.bar(positions - 0.2, current_values, width=0.4, color=RED_BALL, label="当前遗漏")
    ax.bar(positions + 0.2, max_values, width=0.4, color=BLUE_BALL_LIGHT, label="历史最大遗漏")
    ax.set_xticks(positions)
    ax.set_xticklabels(numbers)
    ax.legend(frameon=False, fontsize=9, labelcolor=NEUTRAL)
    _style(ax, "红球遗漏：当前 vs 历史最大", "红球号码", "遗漏期数")
    return _save(fig, out_path)


def plot_odd_even(summary: dict, out_path: Path) -> Path:
    """奇偶比分布。"""
    labels = summary["odd_even"]["labels"]
    counts = summary["odd_even"]["counts"]
    peak = max(counts)
    colors = [RED_BALL if count == peak else RED_BALL_LIGHT for count in counts]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    ax.bar(labels, counts, color=colors, width=0.6)
    _style(ax, "红球奇偶比分布", "奇偶比（奇数个数：偶数个数）", "期数")
    return _save(fig, out_path)


def plot_sum_trend(summary: dict, out_path: Path) -> Path:
    """和值随期次变化，并叠加滑动平均。"""
    values = summary["sums"]["values"]
    window = 20
    if len(values) >= window:
        smoothed = np.convolve(values, np.ones(window) / window, mode="valid")
        smoothed_x = np.arange(window - 1, len(values))
    else:
        smoothed, smoothed_x = [], []

    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    ax.plot(values, color=BLUE_BALL_LIGHT, linewidth=0.9, label="每期和值")
    if len(smoothed):
        ax.plot(smoothed_x, smoothed, color=RED_BALL, linewidth=2.0, label=f"{window} 期滑动平均")
    ax.axhline(summary["sums"]["theoretical_mean"], color=NEUTRAL, linestyle="--", linewidth=1.0)
    ax.text(
        1,
        summary["sums"]["theoretical_mean"] + 3,
        f"理论均值 {summary['sums']['theoretical_mean']:.0f}",
        fontsize=9,
        color=NEUTRAL,
    )
    ax.legend(frameon=False, fontsize=9, labelcolor=NEUTRAL)
    _style(ax, "红球和值走势（左为最老一期，右为最新一期）", "期次序号", "和值")
    return _save(fig, out_path)


def plot_heatmap(summary: dict, out_path: Path) -> Path:
    """号码 × 年份 出现次数热力图。"""
    heatmap = summary["heatmap"]
    matrix = np.array(heatmap["matrix"], dtype=float)

    fig, ax = plt.subplots(figsize=(12, 3.6), dpi=150)
    image = ax.imshow(matrix, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(len(heatmap["numbers"])))
    ax.set_xticklabels([str(number) for number in heatmap["numbers"]], fontsize=8)
    ax.set_yticks(range(len(heatmap["years"])))
    ax.set_yticklabels([str(year) for year in heatmap["years"]], fontsize=9)
    ax.set_xlabel("红球号码", fontsize=10, color=NEUTRAL)
    ax.set_ylabel("年份", fontsize=10, color=NEUTRAL)
    ax.set_title("红球出现次数热力图", fontsize=13, fontweight="bold", color=TITLE_COLOR, pad=12)
    ax.tick_params(colors=NEUTRAL)
    bar = fig.colorbar(image, ax=ax, pad=0.01)
    bar.ax.tick_params(labelsize=8, colors=NEUTRAL)
    return _save(fig, out_path)
