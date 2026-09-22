"""``shuangseqiu.selector`` 的测试：形态条件、历史排除、可复现性与降级路径。

选号规则于 2026-09-23 整体换成**形态条件**（取代旧的「含 32/33 + 和值 ≥120 +
禁 3 连号」）：3 奇 3 偶 / 3 大 3 小 / 三区比 2:2:2 / 恰好 1 组二连号 /
与上一期重号 1 个。每条条件都单独钉住，并用固定随机种子保证可复现——
否则测试本身就会变成随机失败源。
"""

from __future__ import annotations

import datetime as dt
import itertools
import random
from math import comb

import pytest
from expected_data import DISTINCT_RED_GROUPS, DUPLICATE_RED_PAIRS, PERIODS

from shuangseqiu import crowding, selector
from shuangseqiu.dataset import (
    DrawRecord,
    big_count,
    odd_count,
    run_lengths,
    zone_counts,
)

DAY = dt.date(2023, 1, 3)
SEED = 20260923

# 单测用的「上一期」红球。取真实最新一期那组，省得再造一套数字。
UNIT_LAST: tuple[int, ...] = (2, 5, 16, 19, 26, 32)

# 「三区 2:2:2 + 3 大 3 小」逼出的**结构化空间**：第一区取 2、第二区取 1 小 1 大、
# 第三区取 2。见 :func:`test_the_second_zone_split_is_forced`。
# 大小 = C(11,2) × 5 × 6 × C(11,2) = 90 750，比全空间 1 107 568 小一个数量级，
# 于是「穷尽枚举候选空间」在测试里是负担得起的（约 0.15 秒）。
_ZONE1 = list(range(1, 12))
_ZONE2_SMALL = list(range(12, 17))
_ZONE2_BIG = list(range(17, 23))
_ZONE3 = list(range(23, 34))
STRUCTURED_SPACE: tuple[tuple[int, ...], ...] = tuple(
    tuple(sorted(a + (s, b) + c))
    for a in itertools.combinations(_ZONE1, 2)
    for s in _ZONE2_SMALL
    for b in _ZONE2_BIG
    for c in itertools.combinations(_ZONE3, 2)
)


def _satisfies(reds, config, last_reds) -> bool:
    """独立于 :mod:`selector` 内部实现，用 dataset 的公开工具把判定重写一遍。"""
    if len(reds) != 6 or len(set(reds)) != 6:
        return False
    if any(not 1 <= number <= 33 for number in reds):
        return False
    if odd_count(reds) != config.odd_count:
        return False
    if big_count(reds) != config.big_count:
        return False
    if zone_counts(reds) != config.zone_ratio:
        return False
    runs = run_lengths(reds)
    if len(runs) != config.consecutive_groups:
        return False
    if runs and max(runs) > config.max_run:
        return False
    if config.repeat_count is not None:
        if len(set(reds) & set(last_reds)) != config.repeat_count:
            return False
    return True


@pytest.fixture(scope="module")
def strategy(records):
    """基于真实数据集构建的选号器（模块级复用，避免反复重算拥挤度）。"""
    return selector.SelectionStrategy.from_records(records)


@pytest.fixture(scope="module")
def report(records):
    return crowding.compute_crowding(records)


@pytest.fixture(scope="module")
def batch(strategy) -> list[selector.Selection]:
    """一次性抽 300 注，给多个用例共用。

    现在一注要拒绝采样约 240 次（旧规则只要 3.8 次），
    每个用例各抽一遍会把测试拖成十几秒，而它们要的其实是同一批样本。
    """
    rng = random.Random(SEED)
    return [strategy.select(rng) for _ in range(300)]


@pytest.fixture
def tiny_report() -> crowding.CrowdingReport:
    """含 16 个蓝球的极小拥挤度画像，用于不依赖真实数据的单元测试。"""
    day = dt.date(2023, 1, 3)
    sample = [
        DrawRecord(issue=2023001 + offset, date=day, reds=(1, 2, 3, 4, 5, 32 + offset % 2),
                   blue=offset % 16 + 1,
                   sales=crowding.TOTAL_COMBINATIONS * 2 * 8, first_winners=8)
        for offset in range(16 * 8)
    ]
    return crowding.compute_crowding(sample)


# --------------------------------------------------------------------------
# 抽出来的号码必须满足全部形态条件
# --------------------------------------------------------------------------

def test_every_draw_satisfies_every_condition(strategy, batch) -> None:
    config = strategy.config
    last = strategy.last_reds
    for selection in batch:
        assert len(selection.reds) == 6
        assert list(selection.reds) == sorted(selection.reds), "红球必须升序"
        assert len(set(selection.reds)) == 6, "红球不能重复"
        assert all(1 <= number <= 33 for number in selection.reds)
        assert 1 <= selection.blue <= 16
        assert _satisfies(selection.reds, config, last), f"不满足形态条件：{selection.reds}"
        assert not selection.relaxed


def test_each_condition_holds_individually(strategy, batch) -> None:
    """把五条条件逐条单独断言，失败时报出是哪一条不满足。"""
    config = strategy.config
    for selection in batch:
        reds = selection.reds
        assert odd_count(reds) == config.odd_count == 3, f"奇偶比不对：{reds}"
        assert big_count(reds) == config.big_count == 3, f"大小比不对：{reds}"
        assert zone_counts(reds) == config.zone_ratio == (2, 2, 2), f"三区比不对：{reds}"
        runs = run_lengths(reds)
        assert len(runs) == 1, f"连号组数应为 1：{reds}"
        assert max(runs) == 2, f"连号必须是二连（不是三连）：{reds}"
        assert len(set(reds) & set(strategy.last_reds)) == 1, f"重号数应为 1：{reds}"


def test_draws_never_repeat_a_historical_red_set(batch, records) -> None:
    history = {frozenset(record.reds) for record in records}
    for selection in batch:
        assert frozenset(selection.reds) not in history


def test_the_second_zone_split_is_forced() -> None:
    """三区 2:2:2 + 3 大 3 小 ⇒ 第二区必然是「1 个 12-16 + 1 个 17-22」。

    这条引理是 :data:`STRUCTURED_SPACE` 的依据：只有它成立，按结构化空间穷尽枚举
    出来的候选数才等于真实的候选空间大小。所以这里把两个方向都钉住。
    """
    for reds in STRUCTURED_SPACE:
        assert zone_counts(reds) == (2, 2, 2)
        assert big_count(reds) == 3

    # 反方向：随便撒点，凡是满足这两条的都必须能由结构化空间拼出来
    # （集合先建好，别放进循环里，否则每次迭代都要重建 9 万元素）
    structured = set(STRUCTURED_SPACE)
    rng = random.Random(SEED)
    checked = 0
    while checked < 2000:
        reds = tuple(sorted(rng.sample(range(1, 34), 6)))
        if zone_counts(reds) != (2, 2, 2) or big_count(reds) != 3:
            continue
        assert reds in structured, f"{reds} 满足条件却不在结构化空间里"
        checked += 1


def test_candidate_space_matches_a_full_enumeration(strategy, records) -> None:
    """穷尽枚举候选空间，并把三件事对齐。

    1. 枚举出的候选数 == 独立写的 :func:`_satisfies` 判定的通过数；
    2. 再排除历史撞号后，== ``_accepts`` 的通过数；
    3. 量级落在 4 000~5 000（规则被改动时会立刻露馅）。
    """
    history = {frozenset(record.reds) for record in records}
    config = strategy.config
    last = strategy.last_reds

    shape_ok = [reds for reds in STRUCTURED_SPACE if _satisfies(reds, config, last)]
    not_historical = [reds for reds in shape_ok if frozenset(reds) not in history]
    accepts = [reds for reds in STRUCTURED_SPACE if strategy._accepts(reds)]

    assert len(accepts) == len(not_historical)
    assert 4000 <= len(accepts) <= 5000, f"候选空间大小异常：{len(accepts)}"
    # 历史撞号这次是真的会拦下东西了（旧规则下是 0 条）
    assert len(shape_ok) - len(accepts) == len(shape_ok) - len(not_historical)


def test_sampling_rate_agrees_with_the_enumerated_space(strategy, records) -> None:
    """拒绝采样的命中率必须与穷尽枚举出来的一致。

    这是从两个完全不同的方向验证同一件事：一边数格子，一边真抽。
    """
    history = {frozenset(record.reds) for record in records}
    n_accepted = sum(
        1
        for reds in STRUCTURED_SPACE
        if _satisfies(reds, strategy.config, strategy.last_reds) and frozenset(reds) not in history
    )
    enumerated_rate = n_accepted / comb(33, 6)

    rng = random.Random(SEED + 5)
    trials = 60000
    hits = sum(1 for _ in range(trials) if strategy._accepts(tuple(sorted(rng.sample(range(1, 34), 6)))))
    measured_rate = hits / trials

    assert measured_rate == pytest.approx(enumerated_rate, rel=0.2), (
        f"实测命中率 {measured_rate:.5%} 与枚举值 {enumerated_rate:.5%} 差太多"
    )


# --------------------------------------------------------------------------
# 历史排除
# --------------------------------------------------------------------------

def test_history_exclusion_rejects_an_exact_match(strategy, records) -> None:
    """把一条落在候选空间里的历史组合塞进历史库，``_accepts`` 必须拒绝它。"""
    history = {frozenset(record.reds) for record in records}
    target = next(
        reds
        for reds in STRUCTURED_SPACE
        if _satisfies(reds, strategy.config, strategy.last_reds) and frozenset(reds) in history
    )

    free = selector.SelectionStrategy(
        strategy.report, history=[], last_reds=strategy.last_reds
    )
    assert free._accepts(target) is True

    blocked = selector.SelectionStrategy(
        strategy.report, history=[target], last_reds=strategy.last_reds
    )
    assert blocked._accepts(target) is False


def test_history_exclusion_now_actually_fires(strategy, records) -> None:
    """新形态空间把历史撞号从「永不触发」变成了「真会拦下」。

    旧的「含 32/33 且和值 ≥120」空间里，历史那几对重复组合一条都不在里面，
    所以那条规则纯属摆设。换成形态条件后，候选空间小了 240 倍，
    历史组合开始真的落进来——这里把这件事钉住，免得后人沿用旧结论。
    """
    history = {frozenset(record.reds) for record in records}
    inside = [
        reds
        for reds in STRUCTURED_SPACE
        if _satisfies(reds, strategy.config, strategy.last_reds) and frozenset(reds) in history
    ]
    assert len(inside) >= 1, "历史撞号又变成永不触发了，本测试的结论需要重审"
    assert len(inside) < 100, f"拦下的历史组合异常多：{len(inside)}"


def test_history_is_keyed_on_red_balls_only(tiny_report) -> None:
    """排除规则只看红球：历史库登记的就是红球集合本身。"""
    target = (4, 10, 13, 21, 25, 26)
    assert _satisfies(target, selector.SelectionConfig(), UNIT_LAST)
    strategy = selector.SelectionStrategy(
        tiny_report, history=[target], last_reds=UNIT_LAST
    )
    assert strategy.history == frozenset({frozenset(target)})
    assert strategy._accepts(target) is False


# --------------------------------------------------------------------------
# 违规必须被拒
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("reds", "reason"),
    [
        ((4, 11, 13, 21, 25, 26), "4 奇 2 偶"),
        ((4, 10, 13, 15, 25, 26), "2 大 4 小"),
        ((4, 7, 10, 21, 25, 26), "三区 3:1:2"),
        ((4, 10, 13, 21, 23, 26), "没有连号"),
        ((2, 11, 13, 22, 23, 24), "一组三连（要求二连）"),
        ((3, 4, 16, 22, 23, 25), "两组连号"),
        ((4, 10, 13, 21, 27, 28), "没有重号"),
    ],
)
def test_accepts_rejects_each_violation(tiny_report, reds, reason) -> None:
    """每条样例都**只**违反它标注的那一条（用脚本逐条核对过），
    否则测试失败时无法判断是规则写错还是样例写错。
    """
    strategy = selector.SelectionStrategy(
        tiny_report, history=[], last_reds=UNIT_LAST
    )
    assert strategy._accepts(reds) is False, f"应当拒绝：{reason}"


def test_accepts_accepts_a_conforming_ticket(tiny_report) -> None:
    """反过来也要能过：别把规则写严到没有解。"""
    # 3奇3偶 / 3大3小 / 三区2:2:2 / 25-26 一组二连 / 与 UNIT_LAST 重号 26
    conforming = (4, 10, 13, 21, 25, 26)
    strategy = selector.SelectionStrategy(
        tiny_report, history=[], last_reds=UNIT_LAST
    )
    assert _satisfies(conforming, strategy.config, UNIT_LAST)
    assert strategy._accepts(conforming) is True


# --------------------------------------------------------------------------
# 可复现性与降级
# --------------------------------------------------------------------------

def test_same_seed_gives_the_same_number(strategy) -> None:
    first = strategy.select(random.Random(7))
    second = strategy.select(random.Random(7))
    assert first.reds == second.reds and first.blue == second.blue


def test_different_seeds_give_different_numbers(strategy) -> None:
    results = {strategy.select(random.Random(seed)).label for seed in range(40)}
    assert len(results) > 35, "40 次抽样几乎不该重复"


def test_relaxed_flag_when_conditions_are_unreachable(tiny_report) -> None:
    """「6 奇 0 偶 + 3 大 3 小 + 三区 2:2:2」无解（实测穷尽枚举为 0 注）→ 只能降级。"""
    config = selector.SelectionConfig(
        odd_count=6, big_count=3, repeat_count=None, max_attempts=200
    )
    strategy = selector.SelectionStrategy(tiny_report, history=[], config=config)
    selection = strategy.select(random.Random(1))
    assert selection.relaxed is True
    assert selection.attempts == 200
    assert len(selection.reds) == 6, "降级也要返回一注合法号码，而不是空结果"
    assert any("降级" in note for note in selection.notes)


def test_attempts_are_reported_and_stay_within_the_defensive_limit(strategy, batch) -> None:
    """命中率约 0.42%，所以平均尝试约 240 次；``max_attempts`` 只是防御上限。"""
    attempts = [selection.attempts for selection in batch]
    assert all(attempt < strategy.config.max_attempts for attempt in attempts)
    assert 80 < sum(attempts) / len(attempts) < 700
    assert not any(selection.relaxed for selection in batch)


def test_selection_is_fast_enough_for_the_ui(strategy) -> None:
    """单注必须远快于一次点击的容忍度（实测约 1 ms）。"""
    import time

    rng = random.Random(SEED + 6)
    started = time.perf_counter()
    for _ in range(200):
        strategy.select(rng)
    per_draw = (time.perf_counter() - started) / 200
    assert per_draw < 0.05, f"单注耗时 {per_draw * 1000:.1f} ms，界面上会明显卡顿"


# --------------------------------------------------------------------------
# 重号条件依赖上一期
# --------------------------------------------------------------------------

def test_repeat_condition_requires_the_previous_draw(tiny_report) -> None:
    """不给上一期就必须报错，绝不能静默跳过「重号」这条条件。"""
    with pytest.raises(selector.SelectionError, match="上一期"):
        selector.SelectionStrategy(tiny_report, history=[])


def test_repeat_condition_can_be_switched_off(tiny_report) -> None:
    config = selector.SelectionConfig(repeat_count=None)
    strategy = selector.SelectionStrategy(tiny_report, history=[], config=config)
    selection = strategy.select(random.Random(3))
    assert _satisfies(selection.reds, config, ())
    assert selection.repeats == ()


def test_selection_reports_which_numbers_repeated(strategy) -> None:
    rng = random.Random(SEED + 7)
    for _ in range(50):
        selection = strategy.select(rng)
        assert selection.repeats == tuple(sorted(set(selection.reds) & set(strategy.last_reds)))
        assert selection.repeat_count == 1


# --------------------------------------------------------------------------
# 蓝球加权（不受本次形态条件影响）
# --------------------------------------------------------------------------

def test_blue_weights_favour_the_coldest(strategy) -> None:
    weights = strategy.blue_weights()
    assert len(weights) == 16
    assert max(weights, key=weights.get) == 15, "15 号应权重最高（最冷）"
    assert weights[15] > weights[9], "最冷应重于最热"
    assert all(weight > 0 for weight in weights.values())


def test_blue_rank_counts_from_the_coldest(strategy) -> None:
    assert strategy.blue_rank(15) == 1
    assert strategy.blue_rank(14) == 2
    assert strategy.blue_rank(9) == 16


def test_blue_ball_follows_the_measured_crowding(strategy) -> None:
    """抽样 3000 次，四个最冷的蓝球应明显多于四个最热的。

    这里直接调 :meth:`_sample_blue`：红球拒绝采样现在要跑 240 次左右，
    走整条 :meth:`select` 会把这条测试拖慢 400 倍，而蓝球权重跟红球条件无关。
    """
    rng = random.Random(SEED + 8)
    counts = dict.fromkeys(range(1, 17), 0)
    for _ in range(3000):
        counts[strategy._sample_blue(rng)] += 1
    coldest = sum(counts[number] for number in (15, 14, 1, 16))
    hottest = sum(counts[number] for number in (9, 12, 8, 7))
    assert coldest > hottest, f"冷热蓝球出现次数应当拉开差距：{counts}"
    assert counts[15] > counts[9], f"15 号应比 09 号常见得多：{counts}"


def test_whole_selection_also_uses_the_weighted_blue(strategy) -> None:
    """整条链路上蓝球也走加权（抽 120 注，冷的应多于热的）。"""
    rng = random.Random(SEED + 9)
    counts = dict.fromkeys(range(1, 17), 0)
    for _ in range(120):
        counts[strategy.select(rng).blue] += 1
    coldest = sum(counts[number] for number in (15, 14, 1, 16))
    hottest = sum(counts[number] for number in (9, 12, 8, 7))
    assert coldest > hottest, f"整链路蓝球分布不对：{counts}"


# --------------------------------------------------------------------------
# 参数校验与展示
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "config",
    [
        selector.SelectionConfig(odd_count=7),
        selector.SelectionConfig(big_count=6),
        selector.SelectionConfig(zone_ratio=(3, 3, 3)),
        selector.SelectionConfig(zone_ratio=(2, 2, -1)),
        # 三区比把号码锁进固定区间后，大号个数的可达区间就定了：
        # 第一区全是小号，所以要求 1 个大号必然无解；第二区只有 6 个大号，
        # 要求 0 个也必然无解（6 个位置塞进 5 个小号装不下）。
        selector.SelectionConfig(zone_ratio=(6, 0, 0), big_count=1),
        selector.SelectionConfig(zone_ratio=(0, 6, 0), big_count=0),
        selector.SelectionConfig(max_run=1),
        selector.SelectionConfig(max_run=0),
        selector.SelectionConfig(consecutive_groups=4),
        selector.SelectionConfig(repeat_count=-1),
        selector.SelectionConfig(max_attempts=0),
    ],
)
def test_impossible_config_is_rejected_early(tiny_report, config) -> None:
    with pytest.raises(selector.SelectionError):
        selector.SelectionStrategy(
            tiny_report, history=[], config=config, last_reds=UNIT_LAST
        )


def test_selection_label_is_readable(strategy) -> None:
    selection = strategy.select(random.Random(11))
    reds = " ".join(f"{n:02d}" for n in selection.reds)
    assert selection.label == f"{reds} + {selection.blue:02d}"
    assert selection.sum == sum(selection.reds)


def test_notes_quote_the_measured_indices(strategy, report) -> None:
    """说明文案里的数字必须来自重算结果，不许写死。"""
    selection = strategy.select(random.Random(13))
    joined = "\n".join(selection.notes)
    for index in (
        report.odd_even_balanced,
        report.big_small_balanced,
        report.zone_balanced,
        report.one_pair_run,
        report.repeat_with_previous,
    ):
        assert f"{index.index:.3f}" in joined, f"文案里缺少实测指数 {index.index:.3f}"
    assert f"{strategy.history_periods} 期" in joined
    assert f"{len(strategy.history)} 组" in joined
    assert "不提高中奖概率" in joined, "必须把效果边界写在说明里"


def test_notes_report_each_conditions_direction(strategy, report) -> None:
    """文案必须如实写出每条条件的方向，不能只挑好听的讲。

    实测：3 大 3 小 1.066、三区 2:2:2 1.070 显著**偏热**；
    恰好一组二连 0.961、重号 1 个 0.974 显著**偏冷**；3 奇 3 偶不显著。
    两条偏热两条偏冷互相抵消，所以整体方向不能吹。
    """
    joined = "\n".join(strategy.select(random.Random(17)).notes)
    assert report.big_small_balanced.index > 1.0
    assert report.zone_balanced.index > 1.0
    assert report.one_pair_run.index < 1.0
    assert report.repeat_with_previous.index < 1.0
    assert "偏热" in joined and "偏冷" in joined
    assert "大众更爱买" in joined
    assert "无法断言" in joined, "整体方向没有实测支撑，文案里必须讲清楚"


def test_history_periods_and_distinct_groups_are_both_reported(strategy, records) -> None:
    """说明文案里的「期数」与「组数」都不能少，否则界面数字会自相矛盾。"""
    assert strategy.history_periods == len(records) == PERIODS
    assert len(strategy.history) == DISTINCT_RED_GROUPS


def test_select_numbers_convenience_wrapper(records) -> None:
    selection = selector.select_numbers(records, rng=random.Random(5))
    assert len(selection.reds) == 6 and 1 <= selection.blue <= 16


def test_historical_red_set_repeats_are_exactly_what_chance_predicts(records) -> None:
    """历史里确实有红球完全重复的期，但那正是随机该有的数量。

    生日问题：n 期里出现重复对的期望是 ``n(n-1)/(2N)``，N = C(33,6) = 1 107 568。
    实测对数与这个期望的差不到 2 对，完全落在随机涨落内。

    注意：旧的「含 32/33 + 和值 ≥120」空间里，这些重复组合一条都不在里面，
    所以当时「排除历史撞号」连一次都不会触发。换成形态条件后候选空间小了 240 倍，
    这条规则开始真的会拦下号码——见 :func:`test_history_exclusion_now_actually_fires`。
    """
    seen: dict[frozenset, list[int]] = {}
    for record in records:
        seen.setdefault(frozenset(record.reds), []).append(record.issue)
    duplicated = {reds: issues for reds, issues in seen.items() if len(issues) > 1}

    space = comb(33, 6)
    expected_pairs = len(records) * (len(records) - 1) / (2 * space)
    assert len(duplicated) == DUPLICATE_RED_PAIRS
    assert len(seen) == DISTINCT_RED_GROUPS
    assert abs(len(duplicated) - expected_pairs) < 2, (
        f"实测 {len(duplicated)} 对 vs 随机期望 {expected_pairs:.2f} 对，不该差这么多"
    )
