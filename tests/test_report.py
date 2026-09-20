"""报告渲染模块的测试。

重点验证三件事：内嵌进 HTML 的数据是真能被 JSON 解析的、
两份报告的图片引用与图表容器数量对得上，
以及**报告里的数字和结论措辞全部由计算得出**——不写死在模板里。
"""

from __future__ import annotations

import copy
import json

from shuangseqiu.quality import build_quality_report
from shuangseqiu.report import (
    HTML_FILENAME,
    MARKDOWN_FILENAME,
    _chart_payload,
    _low_period_note,
    _p_value_verdict,
    _period_range,
    _serial_gap_sentence,
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


def test_written_html_is_lf_only_and_its_data_parses(tmp_path, data_path):
    """回归测试：落盘必须用 LF，否则内嵌数据整段失效。

    之前在 Windows 上写出的是 CRLF，脚本靠 ``";\\nconst RED"`` 定位数据结尾，
    匹配不到就会把整段 JS 数据当成坏数据，页面所有图表空白。
    """
    produced = build_reports(output_dir=tmp_path, data_path=data_path)

    html_bytes = produced["html"].read_bytes()
    assert b"\r\n" not in html_bytes, "HTML 被写成了 CRLF，JS 数据分隔符会匹配失败"
    assert b"\r\n" not in produced["markdown"].read_bytes(), "Markdown 被写成了 CRLF"

    text = html_bytes.decode("utf-8")
    start = text.index("const DATA = ") + len("const DATA = ")
    end = text.index(";\nconst RED")
    data = json.loads(text[start:end])

    assert data["heatmap"]["years"] == EXPECTED_YEARS
    assert len(data["yearly"]["counts"]) == len(EXPECTED_YEARS)


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


# ------------------------------------------------- 文案必须由计算得出，不许写死
#
# 下面这组测试针对一类具体缺陷：把「数字」或「结论措辞」直接写进报告模板。
# 数据一更新（例如补进新一期）模板不会跟着变，报告就会出现
# 「正文说 A、表格显示 B」的自相矛盾。修法是让文案由计算结果生成，
# 再用测试把「不许再写死」这条约束钉住。


def test_p_value_verdict_wording_follows_the_value():
    """p 值措辞按区间分三档，且数字取自入参。"""
    assert _p_value_verdict(0.004).startswith("p = 0.004 低于 0.05")
    assert _p_value_verdict(0.070).startswith("p = 0.070 已贴近常用的 0.05")
    assert _p_value_verdict(0.310).startswith("p = 0.310 明显高于 0.05")

    # 边界值归入更保守的一侧：不能一上来就说"看不出差别"
    assert "已贴近" in _p_value_verdict(0.05)
    assert "明显高于" in _p_value_verdict(0.10)


def test_report_embeds_the_verdict_of_every_chi_square(draws):
    """正文措辞必须与实际 p 值一致，而不是另写一套。"""
    quality, summary = _context(draws)
    chi = summary["chi_square"]

    html = render_html(quality, summary, "2026-01-01 00:00")
    markdown = render_markdown(quality, summary, "2026-01-01 00:00", FIGURE_KEYS)

    for text in (html, markdown):
        assert _p_value_verdict(chi["red"]["p_value"]) in text
        assert _p_value_verdict(chi["blue"]["p_value"]) in text


def test_report_narrative_follows_recomputed_p_values(draws):
    """回归测试：换一组 p 值，报告文字必须跟着换。

    之前红球 p 值 0.063 是写死在结论段落里的，这里把它改成一个
    不可能与真实结果撞上的假值，报告里要么跟着变、要么就是没改干净。
    """
    quality, summary = _context(draws)
    real_red = summary["chi_square"]["red"]["p_value"]
    real_blue = summary["chi_square"]["blue"]["p_value"]

    faked = copy.deepcopy(summary)
    faked["chi_square"]["red"]["p_value"] = 0.004321  # 落进「低于 0.05」分支
    faked["chi_square"]["blue"]["p_value"] = 0.876543  # 落进「明显高于」分支

    html = render_html(quality, faked, "2026-01-01 00:00")
    markdown = render_markdown(quality, faked, "2026-01-01 00:00", FIGURE_KEYS)

    for text in (html, markdown):
        assert "p = 0.004" in text, "报告的结论文字没有跟着计算结果走"
        assert "p = 0.877" in text
        assert f"p = {real_red:.3f}" not in text, "旧的真实 p 值仍然残留在文字里"
        assert f"p = {real_blue:.3f}" not in text


def test_low_period_note_names_exactly_the_sparse_years(draws):
    """热力图提示里点名的年份，必须就是期数偏少的那些年。"""
    quality, _ = _context(draws)
    counts = sorted(profile.count for profile in quality.profiles)
    median = counts[len(counts) // 2]
    note = _low_period_note(quality, ratio=0.9)

    for profile in quality.profiles:
        entry = f"{profile.year} 年（{profile.count} 期）"
        assert (entry in note) is (profile.count < median * 0.9), (
            f"{profile.year} 年是否该出现在「期数偏少」提示里判断错误"
        )

    # 2003 年只有 89 期，是这份数据里最少的一年，必须被点到
    assert "2003 年（89 期）" in note


def test_serial_gap_sentence_agrees_with_profiles(draws):
    """「零缺号」这句话只有真的零缺号时才允许出现。"""
    quality, _ = _context(draws)
    has_gap = any(profile.missing_indexes for profile in quality.profiles)
    sentence = _serial_gap_sentence(quality)

    assert ("零缺号" in sentence) is (not has_gap)
    if has_gap:
        for profile in quality.profiles:
            if profile.missing_indexes:
                assert f"{profile.year} 年缺 {len(profile.missing_indexes)} 期" in sentence


def test_serial_gap_sentence_spells_out_gaps(make_draw):
    """造一份年内缺号的样本，确认话术会如实报缺，而不是硬说连续。"""
    quality = build_quality_report(
        [make_draw(1, year=2023), make_draw(2, year=2023), make_draw(4, year=2023)]
    )
    sentence = _serial_gap_sentence(quality)

    assert "零缺号" not in sentence
    assert "2023 年缺 1 期" in sentence


def test_period_range_matches_profiles(draws):
    quality, _ = _context(draws)
    counts = [profile.count for profile in quality.profiles]
    assert _period_range(quality) == f"{min(counts)}–{max(counts)}"


def test_reports_drop_the_stale_hardcoded_claims(draws):
    """回归测试：这几处曾经写死在模板里，数据一换就会变成假话。

    - 「均大于 0.05」：红球 p 值本就贴边，一旦跌破 0.05 这句就假了
    - 「2004 年 10 月」：周二实际自 2004-08-24（2004067 期）起加入
    - 「2020 年因疫情」：归因没有数据支撑，已改为只陈述期数偏少
    - 「全部贴合随机假设」「以上四条也全部通过」：绝对化断言
    """
    quality, summary = _context(draws)
    html = render_html(quality, summary, "2026-01-01 00:00")
    markdown = render_markdown(quality, summary, "2026-01-01 00:00", FIGURE_KEYS)

    for text in (html, markdown):
        assert "均大于 0.05" not in text
        assert "2004 年 10 月" not in text
        assert "2020 年因疫情" not in text
        assert "全部贴合随机假设" not in text
        assert "以上四条也全部通过" not in text
