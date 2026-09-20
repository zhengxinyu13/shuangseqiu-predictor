"""``shuangseqiu.selector`` 的测试：约束、历史排除、可复现性与降级路径。

选号是本项目里唯一「产出号码」的地方，所以这里把每一条过滤条件都单独钉住，
并且用固定随机种子保证结果可复现——否则测试本身就会变成随机失败源。
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from shuangseqiu import crowding, selector
from shuangseqiu.dataset import DrawRecord, longest_run

DAY = dt.date(2023, 1, 3)
SEED = 20260920


@pytest.fixture(scope="module")
def strategy(records):
    """基于真实数据集构建的选号器（模块级复用，避免反复重算拥挤度）。"""
    return selector.SelectionStrategy.from_records(records)


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
# 抽出来的号码必须满足全部策略条件
# --------------------------------------------------------------------------

def test_every_draw_satisfies_every_condition(strategy) -> None:
    rng = random.Random(SEED)
    config = strategy.config
    for _ in range(300):
        selection = strategy.select(rng)
        assert len(selection.reds) == 6
        assert list(selection.reds) == sorted(selection.reds), "红球必须升序"
        assert len(set(selection.reds)) == 6, "红球不能重复"
        assert all(1 <= number <= 33 for number in selection.reds)
        assert 1 <= selection.blue <= 16
        assert any(number in config.high_numbers for number in selection.reds), "缺大号尾部"
        assert selection.sum >= config.min_sum
        assert longest_run(selection.reds) <= config.max_run, "出现了被禁止的长连号"
        assert not selection.relaxed


def test_draws_never_repeat_a_historical_red_set(strategy, records) -> None:
    history = {frozenset(record.reds) for record in records}
    rng = random.Random(SEED + 1)
    for _ in range(300):
        assert frozenset(strategy.select(rng).reds) not in history


def test_history_exclusion_rejects_an_exact_match(tiny_report) -> None:
    """把目标组合塞进历史库，``_accepts`` 必须拒绝它。"""
    target = (10, 11, 20, 27, 32, 33)
    assert sum(target) >= selector.HIGH_SUM
    assert longest_run(target) <= 2 and any(n in (32, 33) for n in target)

    free = selector.SelectionStrategy(tiny_report, history=[])
    assert free._accepts(target) is True

    blocked = selector.SelectionStrategy(tiny_report, history=[target])
    assert blocked._accepts(target) is False


def test_history_is_keyed_on_red_balls_only(tiny_report) -> None:
    """排除规则只看红球：历史库登记的就是红球集合本身。"""
    target = (10, 11, 20, 27, 32, 33)
    strategy = selector.SelectionStrategy(tiny_report, history=[target])
    assert strategy.history == frozenset({frozenset(target)})
    assert strategy._accepts(target) is False


@pytest.mark.parametrize(
    ("reds", "reason"),
    [
        ((1, 2, 3, 4, 5, 6), "没有大号尾部"),
        ((7, 10, 14, 18, 22, 32), "和值不足"),
        ((11, 12, 13, 20, 32, 33), "3 连号"),
    ],
)
def test_accepts_rejects_each_violation(tiny_report, reds, reason) -> None:
    strategy = selector.SelectionStrategy(tiny_report, history=[])
    assert strategy._accepts(reds) is False, f"应当拒绝：{reason}"


# --------------------------------------------------------------------------
# 可复现性与降级
# --------------------------------------------------------------------------

def test_same_seed_gives_the_same_number(strategy) -> None:
    first = strategy.select(random.Random(7))
    second = strategy.select(random.Random(7))
    assert first.reds == second.reds and first.blue == second.blue


def test_different_seeds_give_different_numbers(strategy) -> None:
    results = {strategy.select(random.Random(seed)).label for seed in range(30)}
    assert len(results) > 25, "30 次抽样几乎不该重复"


def test_relaxed_flag_when_conditions_are_unreachable(tiny_report) -> None:
    """和值上限只有唯一解，且该解已在历史库里 → 只能降级返回。

    注意这里大多数尝试是**因为和值不够**被拒的，所以不保证一定撞到历史；
    断言只覆盖「跑满上限后降级」这条路径。
    """
    only_solution = (28, 29, 30, 31, 32, 33)
    config = selector.SelectionConfig(min_sum=183, max_run=6, max_attempts=25)
    strategy = selector.SelectionStrategy(tiny_report, history=[only_solution], config=config)
    selection = strategy.select(random.Random(1))
    assert selection.relaxed is True
    assert selection.attempts == 25
    assert len(selection.reds) == 6, "降级也要返回一注合法号码，而不是空结果"
    assert any("降级" in note for note in selection.notes)


def test_attempts_are_reported_and_usually_small(strategy) -> None:
    rng = random.Random(SEED + 2)
    attempts = [strategy.select(rng).attempts for _ in range(200)]
    assert max(attempts) < 50, f"尝试次数异常偏高：{max(attempts)}"
    assert sum(attempts) / len(attempts) < 10


# --------------------------------------------------------------------------
# 蓝球加权
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
    """抽样 3000 次，四个最冷的蓝球应明显多于四个最热的。"""
    rng = random.Random(SEED + 3)
    counts = dict.fromkeys(range(1, 17), 0)
    for _ in range(3000):
        counts[strategy.select(rng).blue] += 1
    coldest = sum(counts[number] for number in (15, 14, 1, 16))
    hottest = sum(counts[number] for number in (9, 12, 8, 7))
    assert coldest > hottest, f"冷热蓝球出现次数应当拉开差距：{counts}"
    assert counts[15] > counts[9], f"15 号应比 09 号常见得多：{counts}"


def test_high_numbers_appear_in_almost_every_draw(strategy) -> None:
    """大号尾部是硬性条件，因此 32/33 几乎每注都出现。"""
    rng = random.Random(SEED + 4)
    hits = sum(1 for _ in range(300) if any(n in (32, 33) for n in strategy.select(rng).reds))
    assert hits == 300


# --------------------------------------------------------------------------
# 参数校验与展示
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "config",
    [
        selector.SelectionConfig(min_sum=200),
        selector.SelectionConfig(min_sum=20),
        selector.SelectionConfig(high_numbers=(34,)),
        selector.SelectionConfig(high_numbers=(1, 2, 3, 4, 5, 6, 7)),
        selector.SelectionConfig(max_run=0),
        selector.SelectionConfig(max_attempts=0),
    ],
)
def test_impossible_config_is_rejected_early(tiny_report, config) -> None:
    with pytest.raises(selector.SelectionError):
        selector.SelectionStrategy(tiny_report, history=[], config=config)


def test_selection_label_is_readable(strategy) -> None:
    selection = strategy.select(random.Random(11))
    reds = " ".join(f"{n:02d}" for n in selection.reds)
    assert selection.label == f"{reds} + {selection.blue:02d}"
    assert selection.sum == sum(selection.reds)


def test_notes_quote_the_measured_indices(strategy, records) -> None:
    """说明文案里的数字必须来自重算结果，不许写死。"""
    report = crowding.compute_crowding(records)
    selection = strategy.select(random.Random(13))
    joined = "\n".join(selection.notes)
    assert f"{report.red_contains_high.index:.3f}" in joined
    assert f"{report.sum_high.index:.3f}" in joined
    assert f"{len(strategy.history)} 期" in joined
    assert "不改变中奖概率" in joined, "必须把效果边界写在说明里"


def test_select_numbers_convenience_wrapper(records) -> None:
    selection = selector.select_numbers(records, rng=random.Random(5))
    assert len(selection.reds) == 6 and 1 <= selection.blue <= 16


def test_historical_red_sets_are_actually_duplicated_in_the_data(records) -> None:
    """用户要求排除「红球全一致」并非空想：3505 期里确实有 6 对完全撞红的期。"""
    seen: dict[frozenset, list[int]] = {}
    for record in records:
        seen.setdefault(frozenset(record.reds), []).append(record.issue)
    duplicated = {reds: issues for reds, issues in seen.items() if len(issues) > 1}
    assert len(duplicated) == 6
    assert len(seen) == 3499
