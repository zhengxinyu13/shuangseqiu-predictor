"""按拥挤度分析结果选一组号码。

策略来自 :mod:`shuangseqiu.crowding` 的实测结论，不是拍脑袋定的：

1. **红球倾向带上 32 / 33**。实测「含 32 或 33」的拥挤指数 0.848，
   而「全部 ≤31」是 1.057 —— 大众不太爱买大号尾部。
2. **和值偏大（≥120）**。实测 0.806；和值 ≤100 则是 1.061。
3. **避开 3 连号及以上**。实测 1.119，是**偏热**形态（这条推翻了
   「大众会避开连号」的常见说法）。
4. **蓝球按拥挤指数加权**，指数越低越优先。16 个蓝球里最冷的是 15 / 14 / 01。
5. **排除与历史完全相同的红球组合**（历史 3505 期全库比对）。

实现用**拒绝采样**：实测上面 1+2+3 三个条件的联合接受率约 26%（加权后），
平均尝试 3.8 次；历史撞号的概率上界约 0.33%。因此采样极快，
``max_attempts`` 只是防御性上限，真跑满时会降级返回并置 ``relaxed=True``，
不会静默假装满足条件。

**必须说清楚的效果边界**：以上做法**不改变中奖概率** —— 每一注的中奖概率
都是 1/17 721 088，与号码长什么样无关。拥挤度只影响中高奖级的**分摊**：
买的人少，中了就少人跟你分。三至六等奖是固定奖金，完全不受影响。
所以这个选号器能做的只有「别和大家撞在一起」，做不到「提高命中率」。
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from math import isnan

from .crowding import (
    HIGH_RED_NUMBERS,
    HIGH_SUM,
    LONG_RUN,
    CrowdingReport,
    compute_crowding,
)
from .dataset import BLUE_NUMBERS, DrawRecord, longest_run

RED_NUMBERS: tuple[int, ...] = tuple(range(1, 34))
RED_COUNT = 6


class SelectionError(RuntimeError):
    """选号参数自相矛盾，无论怎么抽都不可能满足（属于调用错误，不是随机失败）。"""


@dataclass(frozen=True)
class SelectionConfig:
    """选号参数。默认值即 :mod:`shuangseqiu.crowding` 实测得到的偏好。"""

    high_numbers: tuple[int, ...] = HIGH_RED_NUMBERS
    """红球里被视为「大号尾部」的号码，至少选中一个。"""

    min_sum: int = HIGH_SUM
    """红球和值下限。"""

    max_run: int = LONG_RUN - 1
    """允许的最长连号，默认 2（即禁止 3 连号及以上）。"""

    red_high_weight: float = 3.0
    """抽样时大号尾部的权重倍数；调高只是让采样更快，不改变最终过滤条件。"""

    max_attempts: int = 1000
    """拒绝采样的防御性上限。"""


@dataclass(frozen=True)
class Selection:
    """一次选号结果。"""

    reds: tuple[int, ...]
    blue: int
    attempts: int
    history_hits: int
    relaxed: bool
    notes: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str:
        """形如 ``06 11 13 14 20 28 + 16`` 的展示串。"""
        return " ".join(f"{n:02d}" for n in self.reds) + f" + {self.blue:02d}"

    @property
    def sum(self) -> int:
        return sum(self.reds)


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
    """把选号参数、拥挤度画像与历史库绑在一起的选号器。

    Args:
        report: 拥挤度画像，决定蓝球权重与说明文案里的数字。
        history: 需要排除的历史记录。
        config: 选号参数。
    """

    def __init__(
        self,
        report: CrowdingReport,
        history: Iterable[Sequence[int]] = (),
        config: SelectionConfig | None = None,
    ) -> None:
        self.config = config or SelectionConfig()
        self.report = report
        self.history: frozenset[frozenset[int]] = frozenset(
            frozenset(reds) for reds in history
        )
        _validate(self.config, self.report)

    @classmethod
    def from_records(
        cls,
        records: Sequence[DrawRecord],
        config: SelectionConfig | None = None,
    ) -> SelectionStrategy:
        """从开奖记录一次性构建选号器（含拥挤度重算与历史库）。"""
        return cls(
            report=compute_crowding(records, high_numbers=(config or SelectionConfig()).high_numbers),
            history=[record.reds for record in records],
            config=config,
        )

    # -- 权重 ---------------------------------------------------------------

    def blue_weights(self) -> dict[int, float]:
        """蓝球权重 = 1 / 拥挤指数，越冷权重越高。"""
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
        pool = list(RED_NUMBERS)
        highs = set(self.config.high_numbers)
        weights = [
            self.config.red_high_weight if number in highs else 1.0 for number in pool
        ]
        return tuple(sorted(_weighted_choice(pool, weights, RED_COUNT, rng)))

    def _accepts(self, reds: Sequence[int]) -> bool:
        config = self.config
        if not any(number in config.high_numbers for number in reds):
            return False
        if sum(reds) < config.min_sum:
            return False
        if longest_run(reds) > config.max_run:
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
        notes = [
            f"红球含 {'/'.join(str(n) for n in config.high_numbers)}（实测拥挤指数 "
            f"{self.report.red_contains_high.index:.3f}，低于基线 {self.report.baseline.index:.3f}）",
            f"和值 {sum(reds)} ≥ {config.min_sum}（实测拥挤指数 {self.report.sum_high.index:.3f}）",
            f"最长连号 {longest_run(reds)} ≤ {config.max_run}"
            f"（3 连号实测 {self.report.long_run.index:.3f} 偏热，已避开）",
            f"蓝球 {blue:02d} 拥挤指数 {blue_index:.3f}，在 16 个蓝球里第 "
            f"{self.blue_rank(blue)} 冷（不改变中奖概率，只影响中奖后与人分摊的注数）",
            f"已与历史 {len(self.history)} 期比对，红球 6 个完全相同的组合被排除"
            f"（本次因撞历史重抽 {history_hits} 次）",
        ]
        if relaxed:
            notes.append(
                f"注意：尝试 {attempts} 次仍未同时满足全部过滤条件，已降级返回，"
                "该注可能带有偏热形态，建议再点一次「开始选号」"
            )
        return Selection(
            reds=tuple(sorted(reds)),
            blue=blue,
            attempts=attempts,
            history_hits=history_hits,
            relaxed=relaxed,
            notes=tuple(notes),
        )


def _validate(config: SelectionConfig, report: CrowdingReport) -> None:
    """提前挡住不可能满足的参数组合。

    Raises:
        SelectionError: 参数自相矛盾时。
    """
    if not 1 <= len(config.high_numbers) <= RED_COUNT:
        raise SelectionError(f"大号尾部号码个数非法：{config.high_numbers!r}")
    if any(number not in RED_NUMBERS for number in config.high_numbers):
        raise SelectionError(f"大号尾部号码超出 1-33：{config.high_numbers!r}")
    if not 1 <= config.max_run <= RED_COUNT:
        raise SelectionError(f"max_run 非法：{config.max_run}")
    if config.max_attempts < 1:
        raise SelectionError(f"max_attempts 非法：{config.max_attempts}")

    lowest_sum = sum(sorted(RED_NUMBERS)[-RED_COUNT:])  # 28+29+30+31+32+33
    highest_sum = sum(sorted(RED_NUMBERS)[:RED_COUNT])
    if not highest_sum <= config.min_sum <= lowest_sum:
        raise SelectionError(
            f"min_sum={config.min_sum} 不可能达到，合法区间为 {highest_sum}~{lowest_sum}"
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
