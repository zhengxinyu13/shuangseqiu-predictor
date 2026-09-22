"""按「形态条件」选一组号码。

规则由 Grayson 于 2026-09-23 指定，**取代**了此前那套拥挤度驱动的条件
（旧的「必须含 32/33 + 和值 ≥120 + 禁 3 连号」已整体废弃）。当前条件是：

1. **3 奇 3 偶**（红球里奇数恰好 3 个）；
2. **3 大 3 小**（16 为界，大号恰好 3 个）；
3. **三区比 2:2:2**（01-11 / 12-22 / 23-33 各 2 个）；
4. **1 组连号**（恰好一组相邻号码，且这组是二连——三连及以上不算）；
5. **与上一期重号 1 个**（和最近一期红球恰好有 1 个相同）。

实现仍是**拒绝采样**：均匀随机抽 6 个，满足全部条件才要。
从全空间 1 107 568 注里筛出符合条件的 **4 648 注**，命中率约 0.42%，
平均尝试约 240 次（实测单注 3 毫秒量级，界面上感觉不到）。

**必须说清楚的三件事（数字都有实测支撑，见 :mod:`shuangseqiu.crowding`）：**

- **这些形态不提高中奖概率。** 一注的中奖概率恒为 1/17 721 088，与号码形态无关。
  而且 3 奇 3 偶 / 3 大 3 小 / 三区 2:2:2 在历史里各占 35.4% / 35.4% / 15.4%，
  与组合数学算出的 34.4% / 34.4% / 15.0% 一致——**它们出现的频率就是随机本身的频率**，
  没有任何预测力。
- **五条条件在「避热门」上是两个方向，互相抵消**（各条的实测拥挤指数）：

  | 条件 | 指数 | 95% CI | 结论 |
  |---|---|---|---|
  | 3 奇 3 偶 | 1.017 | [0.997, 1.036] | 不显著 |
  | 3 大 3 小 | 1.066 | [1.046, 1.086] | **显著偏热** |
  | 三区 2:2:2 | 1.070 | [1.039, 1.100] | **显著偏热** |
  | 恰好一组二连 | 0.961 | [0.944, 0.978] | **显著偏冷** |
  | 与上一期重号 1 个 | 0.974 | [0.956, 0.991] | **显著偏冷** |

  两条偏热、两条偏冷、一条中性。**合起来的方向无法断言**：全套条件在历史里
  只出现过 16 期，指数 1.108 但 95% CI [0.937, 1.279] 跨过 1。
  相比之下旧的「含 32/33 且和值 ≥120」是 0.777（n=410，显著偏冷）——
  那才是真正的避热门，现在放弃了。
- 所以这套规则的意义是**「按你认可的形态出号」**，不是「更容易中」也不是「更少人分」。
  三至六等奖是固定奖金，本来也不受任何形态影响。

配置入口是 :class:`SelectionConfig`；把 ``repeat_count`` 设为 ``None``
即可关掉「重号」这一条（那一条需要上一期红球，是唯一依赖时间顺序的条件）。
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from math import isnan

from .crowding import (
    BALANCED_COUNT,
    BALANCED_ZONES,
    ONE_PAIR_RUN,
    RED_COMBINATIONS,
    REPEAT_WITH_PREVIOUS,
    CrowdingReport,
    compute_crowding,
)
from .dataset import (
    BIG_THRESHOLD,
    BLUE_NUMBERS,
    ZONES,
    DrawRecord,
    big_count,
    consecutive_runs,
    odd_count,
    run_lengths,
    zone_counts,
)

RED_NUMBERS: tuple[int, ...] = tuple(range(1, 34))
RED_COUNT = 6


class SelectionError(RuntimeError):
    """选号参数自相矛盾，无论怎么抽都不可能满足（属于调用错误，不是随机失败）。"""


@dataclass(frozen=True)
class SelectionConfig:
    """选号条件。默认值即 Grayson 2026-09-23 指定的那套形态条件。"""

    odd_count: int = BALANCED_COUNT
    """红球里奇数的个数（默认 3，即 3 奇 3 偶）。"""

    big_count: int = BALANCED_COUNT
    """红球里大号的个数，以 :data:`shuangseqiu.dataset.BIG_THRESHOLD` 为界（默认 3）。"""

    zone_ratio: tuple[int, int, int] = BALANCED_ZONES
    """三区比（默认 ``(2, 2, 2)``：01-11 / 12-22 / 23-33 各 2 个）。"""

    consecutive_groups: int = 1
    """连号**组数**（默认 1，即恰好一组连号）。"""

    max_run: int = ONE_PAIR_RUN
    """单组连号的最大长度（默认 2：那组必须是二连，**三连及以上会被拒**）。

    想把「1 组连号」放宽成允许三连，把这里改成 3 或更大即可。
    """

    repeat_count: int | None = REPEAT_WITH_PREVIOUS
    """与上一期红球的重号个数（默认 1）。设为 ``None`` 表示不作此限制。"""

    max_attempts: int = 20000
    """拒绝采样的防御性上限。命中率约 0.42%，跑到 20000 次的概率是 0
    （``(1-0.0042)**20000 ≈ e^-84``），所以它纯粹是防御，不会真的用满。"""


@dataclass(frozen=True)
class Selection:
    """一次选号结果。"""

    reds: tuple[int, ...]
    blue: int
    attempts: int
    history_hits: int
    relaxed: bool
    notes: tuple[str, ...] = field(default=())
    repeats: tuple[int, ...] = ()
    """与上一期相同的红球号码（升序）。空元组表示没有重号或没提供上一期。"""

    @property
    def label(self) -> str:
        """形如 ``06 11 13 14 20 28 + 16`` 的展示串。"""
        return " ".join(f"{n:02d}" for n in self.reds) + f" + {self.blue:02d}"

    @property
    def sum(self) -> int:
        return sum(self.reds)

    @property
    def repeat_count(self) -> int:
        """与上一期的重号个数。"""
        return len(self.repeats)


def _weighted_choice(
    pool: list[int],
    weights: list[float],
    count: int,
    rng: random.Random,
) -> list[int]:
    """不放回地按权重抽 ``count`` 个（每次抽完重新归一）。"""
    chosen: list[int] = []
    for _ in range(count):
        total = sum(weights)
        if total <= 0:
            raise SelectionError("权重全为 0，无法抽样")
        threshold = rng.random() * total
        cumulative = 0.0
        for position, weight in enumerate(weights):
            cumulative += weight
            if threshold <= cumulative:
                chosen.append(pool.pop(position))
                weights.pop(position)
                break
    return chosen


class SelectionStrategy:
    """把选号条件、拥挤度画像、上一期号码与历史库绑在一起的选号器。

    Args:
        report: 拥挤度画像，决定蓝球权重与说明文案里的数字。
        history: 需要排除的历史记录。
        config: 选号条件。
        history_periods: 历史期数（历史里红球有完全重复的期，
            所以「期数」与「去重组数」是两个数，说明文案要同时给出）。
        last_reds: 上一期红球，「重号」条件需要它。
            ``config.repeat_count`` 非 ``None`` 时必须提供，否则 :class:`SelectionError`。
    """

    def __init__(
        self,
        report: CrowdingReport,
        history: Iterable[Sequence[int]] = (),
        config: SelectionConfig | None = None,
        history_periods: int | None = None,
        last_reds: Sequence[int] = (),
    ) -> None:
        self.config = config or SelectionConfig()
        self.report = report
        self.history: frozenset[frozenset[int]] = frozenset(
            frozenset(reds) for reds in history
        )
        self.history_periods = len(self.history) if history_periods is None else history_periods
        self.last_reds: frozenset[int] = frozenset(last_reds)
        _validate(self.config, self.report, self.last_reds)

    @classmethod
    def from_records(
        cls,
        records: Sequence[DrawRecord],
        config: SelectionConfig | None = None,
    ) -> SelectionStrategy:
        """从开奖记录一次性构建选号器（含拥挤度重算、历史库与上一期号码）。

        要求 ``records`` 按期号升序（:func:`shuangseqiu.dataset.read_records`
        返回的就是升序），上一期取 ``records[-1]``。
        """
        return cls(
            report=compute_crowding(records),
            history=[record.reds for record in records],
            config=config,
            history_periods=len(records),
            last_reds=records[-1].reds if records else (),
        )

    # -- 权重 ---------------------------------------------------------------

    def blue_weights(self) -> dict[int, float]:
        """蓝球权重 = 1 / 拥挤指数，越冷权重越高。

        蓝球不受本次形态条件影响：Grayson 只规定了红球形态，
        所以蓝球继续按实测冷热度加权。
        """
        weights: dict[int, float] = {}
        for number in BLUE_NUMBERS:
            index = self.report.blue[number].index
            weights[number] = 0.0 if isnan(index) or index <= 0 else 1.0 / index
        return weights

    def blue_rank(self, blue: int) -> int:
        """蓝球按拥挤指数从低到高的排名（1 = 最冷）。"""
        ordered = sorted(BLUE_NUMBERS, key=lambda n: self.report.blue[n].index)
        return ordered.index(blue) + 1

    # -- 采样 ---------------------------------------------------------------

    def _sample_reds(self, rng: random.Random) -> tuple[int, ...]:
        """均匀随机抽 6 个红球（无权重，条件全部交给 :meth:`_accepts` 筛）。"""
        return tuple(sorted(rng.sample(RED_NUMBERS, RED_COUNT)))

    def _accepts(self, reds: Sequence[int]) -> bool:
        config = self.config
        if len(reds) != RED_COUNT or len(set(reds)) != RED_COUNT:
            return False
        if not all(number in RED_NUMBERS for number in reds):
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
            if len(set(reds) & self.last_reds) != config.repeat_count:
                return False
        return frozenset(reds) not in self.history

    def select(self, rng: random.Random | None = None) -> Selection:
        """抽一组号码。

        Returns:
            :class:`Selection`。若尝试次数用尽仍未满足全部条件，
            会返回最后一组候选并置 ``relaxed=True``（而不是抛错或假装满足）。
        """
        random_source = rng or random.Random()
        blue = self._sample_blue(random_source)

        attempts = 0
        history_hits = 0
        candidate: tuple[int, ...] = ()
        while attempts < self.config.max_attempts:
            attempts += 1
            candidate = self._sample_reds(random_source)
            if frozenset(candidate) in self.history:
                history_hits += 1
                continue
            if self._accepts(candidate):
                return self._build(candidate, blue, attempts, history_hits, relaxed=False)

        # 兜底：真跑满上限就降级返回，并在文案里说清楚，绝不静默
        return self._build(candidate, blue, attempts, history_hits, relaxed=True)

    def _sample_blue(self, rng: random.Random) -> int:
        weights = self.blue_weights()
        pool = list(BLUE_NUMBERS)
        return _weighted_choice(pool, [weights[n] for n in pool], 1, rng)[0]

    def _build(
        self,
        reds: Sequence[int],
        blue: int,
        attempts: int,
        history_hits: int,
        relaxed: bool,
    ) -> Selection:
        config = self.config
        blue_index = self.report.blue[blue].index
        repeats = tuple(sorted(set(reds) & self.last_reds))
        runs = run_lengths(reds)

        notes = [
            f"奇偶比 {odd_count(reds)}:{RED_COUNT - odd_count(reds)}"
            f"（奇偶均衡实测拥挤指数 {self.report.odd_even_balanced.index:.3f}，"
            f"与基线 {self.report.baseline.index:.3f} 无显著差异）",
            f"大小比 {big_count(reds)}:{RED_COUNT - big_count(reds)}"
            f"（以 {BIG_THRESHOLD} 为界；该形态实测 {self.report.big_small_balanced.index:.3f}，"
            "显著偏热——大众更爱买）",
            f"三区比 {':'.join(str(c) for c in zone_counts(reds))}"
            f"（01-11/12-22/23-33；该形态实测 {self.report.zone_balanced.index:.3f}，"
            "显著偏热——大众更爱买）",
            f"连号 {len(runs)} 组"
            + (f"（{' '.join(f'{n:02d}' for n in _run_numbers(reds))}）" if runs else "")
            + f"，单组最长 {max(runs) if runs else 1} ≤ {config.max_run}"
            f"（恰好一组二连实测 {self.report.one_pair_run.index:.3f}，显著偏冷）",
        ]
        if config.repeat_count is not None:
            notes.append(
                f"与上一期重号 {len(repeats)} 个"
                + (f"（{' '.join(f'{n:02d}' for n in repeats)}）" if repeats else "")
                + f"（重号 1 个实测 {self.report.repeat_with_previous.index:.3f}，显著偏冷）"
            )
        notes += [
            f"蓝球 {blue:02d} 拥挤指数 {blue_index:.3f}，在 16 个蓝球里第 "
            f"{self.blue_rank(blue)} 冷（不改变中奖概率，只影响中奖后与人分摊的注数）",
            # 撞历史这条刻意不吹：触发率极低，且触发后只是把一注均匀随机换成另一注均匀随机。
            f"已与历史 {self.history_periods} 期（{len(self.history)} 组不同红球组合）比对，"
            f"红球 6 个完全相同的组合被排除"
            f"（本次重抽 {history_hits} 次；历史占红球组合空间仅 "
            f"{len(self.history) / RED_COMBINATIONS:.2%}，触发率很低）",
            "这些形态不提高中奖概率——一注的中奖概率恒为 1/17 721 088，与号码长什么样无关。"
            "在避热门上五条互相抵消：大小与三区偏热、连号与重号偏冷，"
            "合起来方向无法断言（全套形态在历史里只出现 16 期，置信区间跨 1）。",
        ]
        if relaxed:
            notes.append(
                f"注意：尝试 {attempts} 次仍未同时满足全部条件，已降级返回，"
                "该注可能不满足某条形态条件，建议再点一次「开始选号」"
            )
        return Selection(
            reds=tuple(sorted(reds)),
            blue=blue,
            attempts=attempts,
            history_hits=history_hits,
            relaxed=relaxed,
            notes=tuple(notes),
            repeats=repeats,
        )


def _run_numbers(reds: Sequence[int]) -> list[int]:
    """连号段里的号码（展平，用于说明文案）。"""
    numbers: list[int] = []
    for run in consecutive_runs(reds):
        if len(run) >= 2:
            numbers.extend(run)
    return numbers


def _zone_composition(low: int, high: int) -> tuple[int, int, int, int]:
    """一个区间的 ``(大号数, 小号数, 奇数个数, 偶数个数)``。"""
    numbers = range(low, high + 1)
    size = high - low + 1
    big = sum(1 for number in numbers if number >= BIG_THRESHOLD)
    odd = sum(1 for number in numbers if number % 2)
    return big, size - big, odd, size - odd


def _validate(
    config: SelectionConfig,
    report: CrowdingReport,
    last_reds: frozenset[int],
) -> None:
    """提前挡住不可能满足的参数组合。

    这里能做的只是「按区取值上限」这类必要条件检查：三区比定死了每区取几个，
    于是大号个数、奇数个数就各自有了一个可达区间，落在区间外必然无解。
    充分性（是否真存在这样的 6 个号）由测试里的穷尽枚举保证。

    Raises:
        SelectionError: 参数自相矛盾时。
    """
    if not 0 <= config.odd_count <= RED_COUNT:
        raise SelectionError(f"odd_count 非法：{config.odd_count}")
    if not 0 <= config.big_count <= RED_COUNT:
        raise SelectionError(f"big_count 非法：{config.big_count}")
    if (
        len(config.zone_ratio) != 3
        or any(count < 0 for count in config.zone_ratio)
        or sum(config.zone_ratio) != RED_COUNT
    ):
        raise SelectionError(f"zone_ratio 非法：{config.zone_ratio!r}")
    if not 0 <= config.consecutive_groups <= RED_COUNT // 2:
        raise SelectionError(f"consecutive_groups 非法：{config.consecutive_groups}")
    if not ONE_PAIR_RUN <= config.max_run <= RED_COUNT:
        raise SelectionError(f"max_run 非法：{config.max_run}")
    if config.repeat_count is not None:
        if not 0 <= config.repeat_count <= RED_COUNT:
            raise SelectionError(f"repeat_count 非法：{config.repeat_count}")
        if not last_reds:
            raise SelectionError(
                "config.repeat_count 非空时必须提供上一期红球（last_reds）——"
                "否则「重号」条件会被静默跳过"
            )
    if config.max_attempts < 1:
        raise SelectionError(f"max_attempts 非法：{config.max_attempts}")

    # 三区比定死每区取几个，于是「大号个数」「奇数个数」各自有了可达区间。
    # 例：三区比 2:2:2 时第二区只能出 1 个大号（17-22 里挑），
    # 所以大号最少 2 个（第三区）、最多 3 个；要求 3 大 3 小刚好卡在上界。
    min_big = max_big = min_odd = max_odd = 0
    for count, (low, high) in zip(config.zone_ratio, ZONES):
        big_total, small_total, odd_total, even_total = _zone_composition(low, high)
        min_big += max(0, count - small_total)
        max_big += min(count, big_total)
        min_odd += max(0, count - even_total)
        max_odd += min(count, odd_total)
    if not min_big <= config.big_count <= max_big:
        raise SelectionError(
            f"三区比 {config.zone_ratio!r} 取不出 {config.big_count} 个大号"
            f"（只能取出 {min_big}~{max_big} 个）"
        )
    if not min_odd <= config.odd_count <= max_odd:
        raise SelectionError(
            f"三区比 {config.zone_ratio!r} 取不出 {config.odd_count} 个奇数"
            f"（只能取出 {min_odd}~{max_odd} 个）"
        )
    if not any(report.blue[n].index > 0 for n in BLUE_NUMBERS):
        raise SelectionError("全部蓝球拥挤指数均无效，无法加权抽样")


def select_numbers(
    records: Sequence[DrawRecord],
    config: SelectionConfig | None = None,
    rng: random.Random | None = None,
) -> Selection:
    """便捷入口：直接给开奖记录，返回一组号码。"""
    return SelectionStrategy.from_records(records, config).select(rng)
