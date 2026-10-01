"""``shuangseqiu.checker`` 的测试：解析、形态判定、两级历史比对、最小改动建议。

核心不变量只有一条：**校验器和选号器不许有两套规则**。凡是「形态是否合理」
的判定，测试都拿 :meth:`~shuangseqiu.selector.SelectionStrategy.shape_failures`
（或 :meth:`~shuangseqiu.selector.SelectionStrategy.accepts`）当参照物，
而不是在测试里另写一遍规则——否则两边一起写错也测不出来。

大部分用例跑在**自造的极小数据集**上：只有它才能把「历史到底撞了哪一期」
「上一期是谁」这些事钉死。真实数据的端到端行为另有两个用例（走 ``records`` fixture）。
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from shuangseqiu import checker, crowding, selector
from shuangseqiu.dataset import DrawRecord

# 自造的「上一期」红球（3 奇 3 偶 / 3 大 3 小 / 三区 2:2:2，只是构造时没刻意对齐）。
SYNTH_LAST: tuple[int, ...] = (2, 5, 16, 19, 26, 32)

# 满足全部五条形态条件的样例（与上一期重号 26，25-26 一组二连）。
# 逐条核对过：奇偶 3:3、大小 3:3（21/25/26 为大）、三区 2:2:2、恰好一组二连。
CONFORMING: tuple[int, ...] = (4, 10, 13, 21, 25, 26)
CONFORMING_BLUE = 7

# 同样满足全部条件、但**不在**历史里的样例（撞历史的对照组）。
# 把 zone3 从 25-26 挪成 26-27：连号仍在、重号仍只碰 26，但组合换了一组。
FRESH: tuple[int, ...] = (4, 10, 13, 21, 26, 27)

DAY = dt.date(2023, 1, 3)
SEED = 20260923


def _synthetic_records() -> list[DrawRecord]:
    """一份能同时喂给拥挤度重算与校验器的极小数据集。

    前 128 期是「填数」用的（每期都有销售额，保证 16 个蓝球都有实测指数），
    最后一期把 :data:`CONFORMING` 放进历史里，专门用来验「撞历史」那条路径。
    """
    sales = crowding.TOTAL_COMBINATIONS * 2 * 8
    records = [
        DrawRecord(
            issue=2023001 + offset,
            date=DAY,
            reds=(1, 2, 3, 4, 5, 32 + offset % 2),
            blue=offset % 16 + 1,
            sales=sales,
            first_winners=8,
        )
        for offset in range(128)
    ]
    records.append(
        DrawRecord(
            issue=2023001 + 128,
            date=DAY,
            reds=CONFORMING,
            blue=CONFORMING_BLUE,
            sales=sales,
            first_winners=8,
        )
    )
    return records


@pytest.fixture(scope="module")
def synth_records() -> list[DrawRecord]:
    return _synthetic_records()


@pytest.fixture(scope="module")
def synth_report(synth_records):
    return crowding.compute_crowding(synth_records)


@pytest.fixture(scope="module")
def synth_strategy(synth_report, synth_records):
    """基于自造数据集的选号器：历史库与上一期都受控。"""
    return selector.SelectionStrategy(
        synth_report,
        history=[record.reds for record in synth_records],
        history_periods=len(synth_records),
        last_reds=SYNTH_LAST,
    )


@pytest.fixture(scope="module")
def synth_checker(synth_strategy, synth_records) -> checker.TicketChecker:
    return checker.TicketChecker(synth_strategy, synth_records)


@pytest.fixture(scope="module")
def real_checker(records) -> checker.TicketChecker:
    """基于真实数据集的校验器（端到端用例用）。"""
    return checker.TicketChecker.from_records(records)


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "06 11 13 14 20 28 + 16",
        "6 11 13 14 20 28 16",
        "06,11,13,14,20,28,16",
        "06-11-13-14-20-28-16",
        "  06  11  13  14  20  28  16  ",
        "06/11/13/14/20/28/16",
    ],
)
def test_parse_accepts_flexible_separators(text) -> None:
    """分隔符随便写，甚至完全不写——用户不该被格式细节卡住。"""
    reds, blue = checker.parse_ticket(text)
    assert reds == (6, 11, 13, 14, 20, 28)
    assert blue == 16


def test_parse_sorts_the_reds() -> None:
    """红球顺序无所谓，统一升序返回。"""
    reds, blue = checker.parse_ticket("20 11 06 14 28 13 + 16")
    assert reds == (6, 11, 13, 14, 20, 28)
    assert blue == 16


def test_parse_allows_the_blue_to_equal_a_red() -> None:
    """红球与蓝球是两个独立的号码池，13 同时出现是合法的，不能当重复拦掉。"""
    reds, blue = checker.parse_ticket("01 13 17 22 27 33 + 13")
    assert blue == 13
    assert 13 in reds


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("06 11 13 14 20 28", "共 7 个号码"),          # 少一个蓝球
        ("06 11 13 14 20 28 + 16 + 09", "共 7 个号码"),  # 多一个
        ("", "共 7 个号码"),                             # 空输入
        ("06 11 13 14 20 34 16", "01~33"),              # 红球越界
        ("06 11 13 13 20 28 16", "不能重复"),            # 红球重复
        ("06 11 13 14 20 28 17", "01~16"),              # 蓝球越界
    ],
)
def test_parse_errors_say_what_is_wrong(text, fragment) -> None:
    """报错必须指出具体哪里不对，而不是笼统「输入有误」。"""
    with pytest.raises(checker.TicketFormatError) as caught:
        checker.parse_ticket(text)
    assert fragment in str(caught.value), (
        f"报错信息不含 {fragment!r}：{caught.value}"
    )


def test_ticket_size_matches_six_plus_one() -> None:
    assert checker.TICKET_SIZE == selector.RED_COUNT + 1 == 7


# --------------------------------------------------------------------------
# 形态判定：必须与选号器同一份规则
# --------------------------------------------------------------------------

def test_checker_and_selector_never_disagree(synth_strategy, synth_checker) -> None:
    """随机撒 400 注，逐注比对「校验器说合理」与「选号器说合理」。

    这是这个模块最重要的一条测试：两个入口若各写一套规则，
    后果是「选号器抽出来的号码被校验器判为不合理」——比没有校验更糟。
    """
    rng = random.Random(SEED)
    for _ in range(400):
        reds = tuple(sorted(rng.sample(range(1, 34), 6)))
        result = synth_checker.check_numbers(reds, rng.randint(1, 16))
        assert result.balanced == (not synth_strategy.shape_failures(reds)), reds


def test_conforming_ticket_passes_every_condition(synth_checker) -> None:
    result = synth_checker.check_numbers(CONFORMING, 5)
    assert result.balanced is True
    assert result.failed_conditions == ()
    assert len(result.conditions) == 5, "五条形态条件都要逐条报出来"
    assert all(condition.passed for condition in result.conditions)


@pytest.mark.parametrize(
    ("reds", "code", "reason"),
    [
        ((4, 11, 13, 21, 25, 26), "odd_even", "4 奇 2 偶"),
        ((4, 10, 13, 15, 25, 26), "big_small", "2 大 4 小"),
        ((4, 7, 10, 21, 25, 26), "zone", "三区 3:1:2"),
        ((4, 10, 13, 21, 23, 26), "run", "没有连号"),
        ((2, 11, 13, 22, 23, 24), "run", "一组三连（要求二连）"),
        ((4, 10, 13, 21, 27, 28), "repeat", "没有重号"),
    ],
)
def test_failed_condition_is_named_correctly(synth_checker, reds, code, reason) -> None:
    """每条样例都**只**违反标注的那一条（与 test_selector 用的是同一批样例）。"""
    result = synth_checker.check_numbers(reds, 5)
    codes = {condition.code for condition in result.failed_conditions}
    assert code in codes, f"应当报出 {code}（{reason}），实际 {codes}"
    assert result.balanced is False


def test_each_condition_carries_its_measured_index(synth_checker) -> None:
    """默认形态下，每条条件都要带上实测拥挤指数与历史出现率。"""
    result = synth_checker.check_numbers(CONFORMING, 5)
    for condition in result.conditions:
        assert condition.measured is not None, condition.label
        assert f"{condition.measured.index:.3f}" in condition.evidence
        assert condition.rate is not None
        assert condition.trend in {"hot", "cold", "flat"}


def test_measured_index_is_withheld_when_the_config_changes(synth_records) -> None:
    """条件被改成非默认形态时，不许拿默认形态的实测指数去张冠李戴。

    实测指数是按「3 奇 3 偶」算的；用户把条件改成「4 奇 2 偶」之后，
    那个数字就不再对应这条条件了，必须返回 None 并在文案里说清楚。
    """
    config = selector.SelectionConfig(odd_count=4, big_count=3)
    strategy = selector.SelectionStrategy(
        crowding.compute_crowding(synth_records),
        history=[record.reds for record in synth_records],
        config=config,
        history_periods=len(synth_records),
        last_reds=SYNTH_LAST,
    )
    result = checker.TicketChecker(strategy, synth_records).check_numbers(CONFORMING, 5)
    odd_even = next(condition for condition in result.conditions if condition.code == "odd_even")
    assert odd_even.measured is None
    assert odd_even.trend == "flat"
    assert "已改" in odd_even.evidence
    # 其他条件仍是默认值，照常给出指数
    big_small = next(condition for condition in result.conditions if condition.code == "big_small")
    assert big_small.measured is not None


# --------------------------------------------------------------------------
# 两级历史比对
# --------------------------------------------------------------------------

def test_red_only_collision_is_reported_separately(synth_checker) -> None:
    """红球撞上历史、蓝球不同 → 只报「红球全同」，不算「完全相同」。"""
    result = synth_checker.check_numbers(CONFORMING, checker.BLUE_NUMBERS[0])
    assert result.duplicates_history is True
    assert result.duplicates_exactly is False
    assert result.red_matches[0].issue == 2023001 + 128
    # 只有真的完全相同时，「换个蓝球」才是有效建议
    assert result.safe_blues == ()


def test_exact_collision_is_reported_and_suggests_safe_blues(synth_checker) -> None:
    """红球加蓝球都与历史某一期相同 → 报「完全相同」，并给出可换的蓝球。"""
    result = synth_checker.check_numbers(CONFORMING, CONFORMING_BLUE)
    assert result.duplicates_exactly is True
    assert len(result.exact_matches) == 1
    assert CONFORMING_BLUE not in result.safe_blues
    assert set(result.safe_blues) == set(checker.BLUE_NUMBERS) - {CONFORMING_BLUE}


def test_no_collision_reports_nothing(synth_checker) -> None:
    result = synth_checker.check_numbers(FRESH, 5)
    assert result.balanced is True, "对照组本身得是合理的，否则测的不是「不撞历史」"
    assert result.red_matches == ()
    assert result.exact_matches == ()
    assert result.safe_blues == ()
    assert result.duplicates_history is False


def test_real_historical_ticket_matches_itself(real_checker, records) -> None:
    """拿真实历史的一期原样输回去，必须同时命中两级比对。"""
    target = records[-2]  # 拿倒数第二期，避开「最新一期」这类会被别处引用的数字
    result = real_checker.check_numbers(target.reds, target.blue)
    assert result.duplicates_history is True
    assert result.duplicates_exactly is True
    assert any(match.issue == target.issue for match in result.exact_matches)
    assert target.blue not in result.safe_blues


def test_real_checker_reports_the_history_scale(real_checker, records) -> None:
    """历史规模必须与实测一致（期数 + 去重后的红球组数）。"""
    result = real_checker.check_numbers((4, 10, 13, 21, 25, 26), 16)
    assert result.history_periods == len(records)
    assert result.distinct_reds == len({frozenset(record.reds) for record in records})
    assert result.distinct_reds <= result.history_periods


# --------------------------------------------------------------------------
# 最小改动建议
# --------------------------------------------------------------------------

def test_suggestions_change_exactly_one_red_and_stay_valid(synth_checker, synth_strategy) -> None:
    """每条建议都只能是「换一个红球」，且换完之后确实被选号器接受。"""
    original = (4, 11, 13, 21, 25, 26)  # 4 奇 2 偶，需要改
    result = synth_checker.check_numbers(original, 5)
    assert result.suggestions, "该注不满足形态条件，应当给出改法"

    for suggestion in result.suggestions:
        assert suggestion.replace in original
        assert suggestion.with_number not in original
        assert suggestion.blue == 5, "建议只改红球，蓝球必须保持不变"
        assert len(set(suggestion.reds) ^ set(original)) == 2, "必须只差一个号"
        assert suggestion.reds == tuple(sorted(suggestion.reds))
        assert synth_strategy.accepts(suggestion.reds), f"建议出来的号码仍不合理：{suggestion.reds}"


def test_suggestions_are_sorted_by_how_much_the_sum_moves(synth_checker) -> None:
    """改动越小排越前——「最小改动」的意义就是尽量少动原号码的气质。"""
    result = synth_checker.check_numbers((4, 11, 13, 21, 25, 26), 5)
    total = sum((4, 11, 13, 21, 25, 26))
    deltas = [abs(sum(item.reds) - total) for item in result.suggestions]
    assert deltas == sorted(deltas)


def test_no_suggestions_when_nothing_is_wrong(synth_checker) -> None:
    """形态全满足、又不撞历史时，不该硬凑出改法来（没病就不开药）。"""
    result = synth_checker.check_numbers(FRESH, 5)
    assert result.balanced is True
    assert result.red_matches == ()
    assert result.suggestions == ()


def test_suggestions_are_offered_for_a_duplicate_even_if_the_shape_is_fine(synth_checker) -> None:
    """形态没问题但撞了历史 → 仍要给改法，且改法必须避开历史库。"""
    result = synth_checker.check_numbers(CONFORMING, 5)
    assert result.balanced is True
    assert result.duplicates_history is True
    assert result.suggestions, "撞了历史也该给改法"
    for suggestion in result.suggestions:
        assert not synth_checker.duplicates_history_for(suggestion.reds)


def test_suggestion_enumeration_is_cheap(synth_checker) -> None:
    """162 次判定的量级，界面上必须是毫秒级。"""
    import time

    started = time.perf_counter()
    for _ in range(20):
        synth_checker.check_numbers((4, 11, 13, 21, 25, 26), 5)
    assert (time.perf_counter() - started) / 20 < 0.05


def test_showcase_historical_collision_produces_a_different_number(real_checker, records) -> None:
    """端到端：真实历史的号码拿去「改号」，给出的建议必须仍然是合理且不撞历史的。

    这里刻意**不**断言「某一期一定有建议」——单号替换的候选只有 6×27=162 个，
    而满足形态的概率约 0.42%，期望上每期只有约 0.7 条建议，具体某期可能一条都没有。
    所以改成扫最近 30 期：有建议就逐条验证，并断言 30 期里至少有一期给得出
    （30 期全都给不出，概率约 0.5³⁰，只可能是逻辑坏了）。
    """
    checked = 0
    with_suggestions = 0
    for record in records[-30:]:
        result = real_checker.check_numbers(record.reds, record.blue)
        assert result.duplicates_history is True, f"{record.issue} 期应当撞历史"
        if result.suggestions:
            with_suggestions += 1
        for suggestion in result.suggestions:
            assert suggestion.blue == record.blue, "建议只改红球"
            assert len(set(suggestion.reds) ^ set(record.reds)) == 2, "必须只差一个号"
            assert real_checker.strategy.shape_failures(suggestion.reds) == (), suggestion.reds
            assert not real_checker.duplicates_history_for(suggestion.reds)
        checked += 1
    assert checked == 30
    assert with_suggestions >= 1, "最近 30 期一条改法都给不出，建议逻辑很可能坏了"


# --------------------------------------------------------------------------
# 统计画像
# --------------------------------------------------------------------------

def test_portraits_cover_the_four_axes(synth_checker) -> None:
    result = synth_checker.check_numbers(CONFORMING, 5)
    labels = [portrait.label for portrait in result.portraits]
    assert labels == ["和值", "跨度", "蓝球", "历史占位"]


def test_portrait_numbers_come_from_the_data(real_checker, records) -> None:
    """画像里的数字必须是算出来的，不是写死的。"""
    result = real_checker.check_numbers((4, 10, 13, 21, 25, 26), 15)
    joined = "\n".join(portrait.line for portrait in result.portraits)

    # 和值画的「历史均值」必须等于真算出来的均值，容一位小数
    mean = sum(record.red_sum for record in records) / len(records)
    assert f"{mean:.1f}" in joined, f"和值画像里没有真实均值 {mean:.1f}"
    assert f"{result.history_periods} 期" in joined

    # 蓝球排名必须来自选号器的口径（1 = 最冷），不能另算一套
    blue_note = next(item.note for item in result.portraits if item.label == "蓝球")
    assert f"第 {real_checker.strategy.blue_rank(15)} 冷" in blue_note

    # 跨度画像的中位数同样来自真实数据
    spans = sorted(record.span for record in records)
    middle = spans[len(spans) // 2] if len(spans) % 2 else (
        spans[len(spans) // 2 - 1] + spans[len(spans) // 2]
    ) / 2
    span_note = next(item.note for item in result.portraits if item.label == "跨度")
    assert f"{middle:.0f}" in span_note


def test_check_result_label_is_padded_and_sorted(synth_checker) -> None:
    result = synth_checker.check("6 11 13 14 20 28 16")
    assert result.label == "06 11 13 14 20 28 + 16"
    assert result.total == sum(result.reds) == 92
    assert result.span == 22


# --------------------------------------------------------------------------
# 报告文案
# --------------------------------------------------------------------------

def test_report_lists_conditions_portraits_and_history(synth_checker) -> None:
    lines = checker.render_report(synth_checker.check("04 11 13 21 25 26 05"))
    body = "\n".join(lines)
    assert "校验号码：04 11 13 21 25 26 + 05" in body
    assert "形态条件：" in body
    assert "统计画像" in body
    assert "历史比对" in body
    assert "最小改动建议" in body


def test_report_always_carries_the_honesty_disclaimer(synth_checker) -> None:
    """效果边界必须每次都说，不能因为这一注「形态很好」就省略。"""
    for text in ("04 10 13 21 26 27 05", "04 11 13 21 25 26 05"):
        body = "\n".join(checker.render_report(synth_checker.check(text)))
        assert "不提高中奖概率" in body
        assert "17 721 088" in body


def test_report_says_when_no_change_is_needed(synth_checker) -> None:
    body = "\n".join(checker.render_report(synth_checker.check("04 10 13 21 26 27 05")))
    assert "不需要" in body


def test_report_mentions_safe_blues_on_an_exact_collision(synth_checker) -> None:
    body = "\n".join(
        checker.render_report(synth_checker.check_numbers(CONFORMING, CONFORMING_BLUE))
    )
    assert "历史开出过一模一样的号码" in body
    assert "只改蓝球就能避开重复" in body
