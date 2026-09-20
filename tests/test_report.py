"""报告渲染模块的测试。

重点验证两件事：内嵌进 HTML 的数据是真能被 JSON 解析的，
以及两份报告的图片引用、图表容器数量与实际渲染需求对得上。
"""

from __future__ import annotations

import json

from shuangseqiu.quality import build_quality_report
from shuangseqiu.report import (
    HTML_FILENAME,
    MARKDOWN_FILENAME,
    _chart_payload,
    build_reports,
    render_html,
    render_markdown,
)
from shuangseqiu.stats import build_summary

CHART_CONTAINERS = (
    "chart-yearly",
    "chart-red",
    "chart-blue",
    "chart-sumhist",
    "chart-sumtrend",
    "chart-oddeven",
    "chart-bigsmall",
    "chart-zones",
    "chart-omission",
    "chart-heatmap",
)

FIGURE_KEYS = {
    "yearly": "figures/01.png",
    "red": "figures/02.png",
    "blue": "figures/03.png",
    "sum": "figures/04.png",
    "trend": "figures/05.png",
    "odd": "figures/06.png",
    "zone": "figures/07.png",
    "omission": "figures/08.png",
    "heatmap": "figures/09.png",
}

# 全量数据集覆盖的年份：2003–2026，无断档
EXPECTED_YEARS = list(range(2003, 2027))


def _context(draws):
    quality = build_quality_report(draws)
    return quality, build_summary(quality.trusted_draws)


def test_chart_payload_is_json_serializable(draws):
    quality, summary = _context(draws)
    restored = json.loads(json.dumps(_chart_payload(quality, summary), ensure_ascii=False))

    assert restored["yearly"]["years"] == EXPECTED_YEARS
    assert restored["yearly"]["suspect"] == [False] * len(EXPECTED_YEARS)
    assert restored["yearly"]["limit"] == 160
    assert len(restored["redFreq"]["counts"]) == 33
    assert len(restored["blueFreq"]["counts"]) == 16
    assert len(restored["omission"]["current"]) == 33
    assert len(restored["omission"]["max"]) == 33
    assert len(restored["heatmap"]["matrix"]) == len(EXPECTED_YEARS)


def test_render_html_embeds_parsable_data(draws):
    quality, summary = _context(draws)
    html = render_html(quality, summary, "2026-01-01 00:00")

    assert html.startswith("<!DOCTYPE html>")
    assert 'src="assets/echarts.min.js"' in html

    start = html.index("const DATA = ") + len("const DATA = ")
    end = html.index(";\nconst RED")
    data = json.loads(html[start:end])

    assert data["redFreq"]["numbers"][0] == 1
    assert data["redFreq"]["numbers"][-1] == 33
    assert len(data["omission"]["current"]) == 33
    assert data["heatmap"]["years"] == EXPECTED_YEARS


def test_render_html_contains_every_chart_container(draws):
    quality, summary = _context(draws)
    html = render_html(quality, summary, "2026-01-01 00:00")

    for chart_id in CHART_CONTAINERS:
        assert f'id="{chart_id}"' in html, f"缺少图表容器 {chart_id}"


def test_render_markdown_references_every_figure(draws):
    quality, summary = _context(draws)
    markdown = render_markdown(quality, summary, "2026-01-01 00:00", FIGURE_KEYS)

    assert markdown.startswith("# 双色球历史开奖数据分析报告")
    assert markdown.count("![") == len(FIGURE_KEYS)
    for path in FIGURE_KEYS.values():
        assert path in markdown


def test_render_markdown_carries_key_findings(draws):
    quality, summary = _context(draws)
    markdown = render_markdown(quality, summary, "2026-01-01 00:00", FIGURE_KEYS)

    assert str(quality.total_records) in markdown  # 3505
    assert str(quality.trusted_count) in markdown  # 3505
    assert "三源交叉校验" in markdown
    assert "55128.cn" in markdown  # 说明为何弃用该数据源
    assert f"{summary['chi_square']['red']['p_value']:.3f}" in markdown


def test_reports_drop_the_stale_unusable_data_verdict(draws):
    """回归测试：数据换成全量后，报告不能再宣称有大片记录不可用。

    旧版报告把「86.5% 不能用 / 2007–2021 断档」写死在模板里，
    数据源换成三源校验过的全量后，这些说法必须彻底消失。
    """
    quality, summary = _context(draws)
    assert quality.suspect_count == 0

    html = render_html(quality, summary, "2026-01-01 00:00")
    markdown = render_markdown(quality, summary, "2026-01-01 00:00", FIGURE_KEYS)

    for text in (html, markdown):
        assert "86.5%" not in text
        assert "物理上不可能" not in text
        assert "2007–2021" not in text
        assert str(quality.total_records) in text


def test_build_reports_writes_all_artifacts(tmp_path, data_path):
    produced = build_reports(output_dir=tmp_path, data_path=data_path)

    assert produced["html"].name == HTML_FILENAME
    assert produced["markdown"].name == MARKDOWN_FILENAME
    for key in FIGURE_KEYS:
        assert produced[key].is_file(), f"图表 {key} 未生成"
        assert produced[key].stat().st_size > 1000, f"图表 {key} 体积异常"

    html = produced["html"].read_text(encoding="utf-8")
    markdown = produced["markdown"].read_text(encoding="utf-8")
    assert "双色球历史开奖数据分析报告" in html
    assert "双色球历史开奖数据分析报告" in markdown
