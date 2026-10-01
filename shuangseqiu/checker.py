"""校验用户自己输入的一注号码：格式、是否与历史重复、形态是否合理。

对应界面上的「校验号码」按钮：把自选的一注敲进去，回车后拿到一份体检报告。

三件事，按需求顺序：

1. **格式**：能否解析成「6 个不重复红球（01-33）+ 1 个蓝球（01-16）」。
   不合规时指出到底哪里不对，而不是笼统说「输入有误」。
2. **是否与历史中奖号码重复**：分两级报——「红球 6 个全同」（选号器排除的就是
   这一级）与「红球加蓝球全同」（真正意义上和历史某一期开出过一模一样的号码）。
3. **是否合理**：按选号器那五条形态条件逐条判定，再附统计画像（和值在历史分布
   里的分位、跨度、蓝球冷热、历史撞号的概率面）；不满足时给**单号替换**的最小
   改动建议。

判定标准与 :mod:`shuangseqiu.selector` **共用同一份**：形态条件取自同一个
:class:`~shuangseqiu.selector.SelectionConfig`，判定走
:meth:`~shuangseqiu.selector.SelectionStrategy.shape_failures`。这样不会出现
「选号器认为合理、校验器认为不合理」的两套规则打架——那比没有校验更糟。

诚实声明（与整个项目一致）：**满足形态不等于更容易中奖**。一注的中奖概率恒为
``1/17 721 088``；形态只决定「是不是按你认可的样子出号」，拥挤度只影响中奖后
与人分摊的注数。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median

from .crowding import (
    BALANCED_COUNT,
    BALANCED_ZONES,
    ONE_PAIR_RUN,
    RED_COMBINATIONS,
    REPEAT_WITH_PREVIOUS,
    TOTAL_COMBINATIONS,
    CrowdingIndex,
    direction,
    direction_text,
)
from .dataset import (
    BIG_THRESHOLD,
    BLUE_NUMBERS,
    RED_NUMBERS,
    DrawRecord,
    big_small_text,
    odd_even_text,
    run_lengths,
    streak_text,
    zone_text,
)
from .selector import RED_COUNT, SelectionConfig, SelectionStrategy

__all__ = [
    "ConditionVerdict",
    "HistoryMatch",
    "Portrait",
    "Suggestion",
    "TicketCheck",
    "TicketChecker",
    "TicketFormatError",
    "parse_ticket",
    "render_report",
]

# 一注的构成：6 个红球 + 1 个蓝球。红球与蓝球是两个独立的号码池，
# 所以蓝球和某个红球重复是合法的，不能当错误拦下来。
TICKET_SIZE = RED_COUNT + 1

# 输入解析：连续数字按两位切分，因此下面这些写法都能吃下——
#   ``06 11 13 14 20 28 + 16``（界面上的标准写法）
#   ``6 11 13 14 20 28 16``
#   ``06,11,13,14,20,28,16``
#   ``0611131420 28 16``
_NUMBER = re.compile(r"\d{1,2}")

# 报告里最多列几条改法（总共多少种会在正文里说明，不藏信息）。
SUGGESTION_PREVIEW = 6


class TicketFormatError(ValueError):
    """输入的号码无法解析成合法的一注（6 个不重复红球 + 1 个蓝球）。"""


def _join(numbers: Sequence[int]) -> str:
    """把号码排成 ``01、05、09`` 的样子。"""
    return "、".join(f"{number:02d}" for number in numbers)


def _validate_numbers(reds: Sequence[int], blue: int) -> tuple[tuple[int, ...], int]:
    """把号码归一成 ``(红球升序元组, 蓝球)``，不合法就抛错。

    Raises:
        TicketFormatError: 个数不对、越界或红球重复时。
    """
    red_part = tuple(int(number) for number in reds)
    if len(red_part) != RED_COUNT:
        raise TicketFormatError(f"红球必须是 {RED_COUNT} 个，实际 {len(red_part)} 个")

    out_of_range = [number for number in red_part if not 1 <= number <= 33]
    if out_of_range:
        raise TicketFormatError(f"红球必须在 01~33 之间，这些越界了：{_join(out_of_range)}")

    duplicates = sorted({number for number in red_part if red_part.count(number) > 1})
    if duplicates:
        raise TicketFormatError(f"红球不能重复，重复的是：{_join(duplicates)}")

    blue_number = int(blue)
    if not 1 <= blue_number <= 16:
        raise TicketFormatError(f"蓝球必须在 01~16 之间，实际是 {blue_number:02d}")

    return tuple(sorted(red_part)), blue_number


def parse_ticket(text: str) -> tuple[tuple[int, ...], int]:
    """把用户输入的号码串解析成 ``(红球升序元组, 蓝球)``。

    前 6 个数字是红球，第 7 个是蓝球；分隔符随意（空格、逗号、``+``、或完全不写，
    见 :data:`_NUMBER` 的说明）。

    Args:
        text: 用户输入的号码串。

    Returns:
        红球升序元组与蓝球号码。

    Raises:
        TicketFormatError: 数字个数不是 7 个，或红球越界 / 重复、蓝球越界时。
    """
    numbers = [int(token) for token in _NUMBER.findall(text or "")]
    if len(numbers) != TICKET_SIZE:
        raise TicketFormatError(
            f"需要 6 个红球 + 1 个蓝球，共 {TICKET_SIZE} 个号码，"
            f"实际解析出 {len(numbers)} 个。可以写成「06 11 13 14 20 28 + 16」这样。"
        )
    *red_part, blue = numbers
    return _validate_numbers(red_part, blue)


@dataclass(frozen=True)
class ConditionVerdict:
    """一条形态条件的判定结果。"""

    code: str
    """条件代号：``odd_even`` / ``big_small`` / ``zone`` / ``run`` / ``repeat``。"""

    label: str
    """条件名，按当前 :class:`SelectionConfig` 渲染，如 ``3 奇 3 偶``。"""

    actual: str
    """本注的实际值，如 ``3:3``。"""

    expected: str
    """条件要求的取值，如 ``3:3``。"""

    passed: bool

    measured: CrowdingIndex | None
    """该形态的实测拥挤指数；条件被改成非默认形态时为 ``None``（不能张冠李戴）。"""

    periods: int
    """该形态在**可用期数**里出现了多少期。"""

    rate: float | None
    """该形态的历史出现率（``periods / 可用期数``）。"""

    @property
    def trend(self) -> str:
        """显著方向：``hot`` / ``cold`` / ``flat``（无实测指数时一律 ``flat``）。"""
        return "flat" if self.measured is None else direction(self.measured)

    @property
    def evidence(self) -> str:
        """括号里的证据：实测拥挤指数 + 历史出现率 + 显著方向。"""
        if self.measured is None:
            return "（当前条件已改，历史上没有对应形态的实测指数）"
        head = f"实测拥挤指数 {self.measured.index:.3f}"
        if self.rate is not None:
            head += f"，历史 {self.periods} 期 / {self.rate:.1%}"
        return f"（{head}，{direction_text(self.measured)}）"

    @property
    def line(self) -> str:
        """渲染成一行，供日志与测试共用。"""
        verdict = "符合" if self.passed else f"不符合（需要 {self.expected}）"
        return f"    · {self.label} {self.actual}：{verdict}{self.evidence}"


@dataclass(frozen=True)
class Portrait:
    """一条统计画像。"""

    label: str
    value: str
    note: str

    @property
    def line(self) -> str:
        return f"    · {self.label} {self.value}：{self.note}"


@dataclass(frozen=True)
class HistoryMatch:
    """一条与输入号码撞上的历史记录。"""

    issue: int
    date: date
    blue: int

    @property
    def label(self) -> str:
        return f"{self.issue}期（{self.date}）"


@dataclass(frozen=True)
class Suggestion:
    """一条「改一个号就合理了」的建议。"""

    replace: int
    with_number: int
    reds: tuple[int, ...]
    blue: int

    @property
    def label(self) -> str:
        return " ".join(f"{number:02d}" for number in self.reds) + f" + {self.blue:02d}"

    @property
    def line(self) -> str:
        return f"    · 把 {self.replace:02d} 换成 {self.with_number:02d} → {self.label}"


@dataclass(frozen=True)
class TicketCheck:
    """一注号码的校验结果。"""

    reds: tuple[int, ...]
    blue: int
    conditions: tuple[ConditionVerdict, ...]
    portraits: tuple[Portrait, ...]
    red_matches: tuple[HistoryMatch, ...]
    """红球 6 个完全相同的历史期（选号器排除的就是这一级）。"""

    exact_matches: tuple[HistoryMatch, ...]
    """红球与蓝球都完全相同的历史期——历史上真的开出过一模一样的号码。"""

    safe_blues: tuple[int, ...]
    """红球保持不变时，换成这些蓝球就不会与历史完全重复。无重复时为空。"""

    suggestions: tuple[Suggestion, ...]
    """单号替换即可满足全部形态条件（且不与历史重复）的改法，按和值改动升序。"""

    history_periods: int
    distinct_reds: int
    """历史里出现过多少组不同的红球组合。"""

    @property
    def label(self) -> str:
        """形如 ``06 11 13 14 20 28 + 16`` 的展示串。"""
        return " ".join(f"{number:02d}" for number in self.reds) + f" + {self.blue:02d}"

    @property
    def total(self) -> int:
        return sum(self.reds)

    @property
    def span(self) -> int:
        return max(self.reds) - min(self.reds)

    @property
    def failed_conditions(self) -> tuple[ConditionVerdict, ...]:
        return tuple(condition for condition in self.conditions if not condition.passed)

    @property
    def balanced(self) -> bool:
        """形态条件是否**全部**满足（不含「是否与历史重复」）。"""
        return not self.failed_conditions

    @property
    def duplicates_history(self) -> bool:
        """红球是否与历史某一期完全相同。"""
        return bool(self.red_matches)

    @property
    def duplicates_exactly(self) -> bool:
        """红球与蓝球是否都与历史某一期完全相同。"""
        return bool(self.exact_matches)


class TicketChecker:
    """把选号器、历史库与统计画像绑在一起的号码校验器。

    Args:
        strategy: 选号器（形态条件、历史库、上一期号码、拥挤度画像都从它取，
            保证判定标准与「开始选号」完全同一份）。
        records: 开奖记录，用于历史比对与统计画像，顺序不限。
        history_periods: 历史期数；不给就用 ``records`` 的长度。
    """

    def __init__(
        self,
        strategy: SelectionStrategy,
        records: Sequence[DrawRecord],
        history_periods: int | None = None,
    ) -> None:
        self.strategy = strategy
        self.config: SelectionConfig = strategy.config
        self.report = strategy.report

        ordered = sorted(records, key=lambda record: record.issue)
        self.records: tuple[DrawRecord, ...] = tuple(ordered)
        self.history_periods = len(ordered) if history_periods is None else history_periods

        self._sums: list[int] = sorted(record.red_sum for record in ordered)
        self._sum_mean = sum(self._sums) / len(self._sums) if self._sums else 0.0
        self._sum_range = self._deciles()
        self._span_median = median([record.span for record in ordered]) if ordered else 0.0

        by_reds: dict[frozenset[int], list[DrawRecord]] = {}
        for record in ordered:
            by_reds.setdefault(frozenset(record.reds), []).append(record)
        self._by_reds = by_reds

    @classmethod
    def from_records(
        cls,
        records: Sequence[DrawRecord],
        config: SelectionConfig | None = None,
    ) -> TicketChecker:
        """从开奖记录一次性构建校验器（含拥挤度重算与历史库）。

        要求 ``records`` 按期号升序（:func:`shuangseqiu.dataset.read_records`
        返回的就是升序），上一期取 ``records[-1]``。
        """
        return cls(SelectionStrategy.from_records(records, config), records)

    # -- 对外入口 -----------------------------------------------------------

    def check(self, text: str) -> TicketCheck:
        """解析并校验用户输入的一注号码。

        Raises:
            TicketFormatError: 输入无法解析成合法的一注时。
        """
        reds, blue = parse_ticket(text)
        return self.check_numbers(reds, blue)

    def check_numbers(self, reds: Sequence[int], blue: int) -> TicketCheck:
        """校验已经拆好的一注号码。

        Raises:
            TicketFormatError: 号码不合法时。
        """
        red_number, blue_number = _validate_numbers(reds, blue)
        matches = tuple(self._by_reds.get(frozenset(red_number), ()))
        history_matches = tuple(
            HistoryMatch(issue=record.issue, date=record.date, blue=record.blue)
            for record in matches
        )
        exact = tuple(match for match in history_matches if match.blue == blue_number)
        # 只有真的撞上同一组红球时，「换个蓝球」才是有意义的建议
        safe_blues = (
            tuple(n for n in BLUE_NUMBERS if all(match.blue != n for match in history_matches))
            if exact
            else ()
        )
        return TicketCheck(
            reds=red_number,
            blue=blue_number,
            conditions=self._conditions(red_number),
            portraits=self._portraits(red_number, blue_number),
            red_matches=history_matches,
            exact_matches=exact,
            safe_blues=safe_blues,
            suggestions=self._suggest(red_number, blue_number),
            history_periods=self.history_periods,
            distinct_reds=len(self._by_reds),
        )

    # -- 形态条件 -----------------------------------------------------------

    def _conditions(self, reds: Sequence[int]) -> tuple[ConditionVerdict, ...]:
        """逐条判定形态条件。通过与否直接问选号器，不在这里另写一套规则。"""
        config = self.config
        failed = set(self.strategy.shape_failures(reds))
        repeat_count = len(set(reds) & set(self.strategy.last_reds))
        runs = run_lengths(reds)
        run_actual = "无连号" if not runs else (
            f"{len(runs)} 组（{streak_text(reds) or ''}，最长 {max(runs)} 连）"
        )

        specs: tuple[tuple[str, str, str, str], ...] = (
            (
                "odd_even",
                f"{config.odd_count} 奇 {RED_COUNT - config.odd_count} 偶",
                odd_even_text(reds),
                f"{config.odd_count}:{RED_COUNT - config.odd_count}",
            ),
            (
                "big_small",
                f"{config.big_count} 大 {RED_COUNT - config.big_count} 小"
                f"（以 {BIG_THRESHOLD} 为界）",
                big_small_text(reds),
                f"{config.big_count}:{RED_COUNT - config.big_count}",
            ),
            (
                "zone",
                f"三区比 {':'.join(str(count) for count in config.zone_ratio)}",
                zone_text(reds),
                ":".join(str(count) for count in config.zone_ratio),
            ),
            (
                "run",
                f"恰好 {config.consecutive_groups} 组连号、最长 {config.max_run} 连",
                run_actual,
                f"{config.consecutive_groups} 组二连",
            ),
        )
        conditions = [
            self._verdict(code, label, actual, expected, code not in failed)
            for code, label, actual, expected in specs
        ]
        if config.repeat_count is not None:
            conditions.append(
                self._verdict(
                    "repeat",
                    f"与上一期重号 {config.repeat_count} 个",
                    f"{repeat_count} 个",
                    f"{config.repeat_count} 个",
                    "repeat" not in failed,
                )
            )
        return tuple(conditions)

    def _verdict(
        self,
        code: str,
        label: str,
        actual: str,
        expected: str,
        passed: bool,
    ) -> ConditionVerdict:
        measured = self._measured_index(code)
        usable = self.report.usable_periods
        return ConditionVerdict(
            code=code,
            label=label,
            actual=actual,
            expected=expected,
            passed=passed,
            measured=measured,
            periods=0 if measured is None else measured.periods,
            rate=None if (measured is None or not usable) else measured.periods / usable,
        )

    def _measured_index(self, code: str) -> CrowdingIndex | None:
        """取该形态的实测指数。

        实测指数是按**默认形态**（3 奇 3 偶 / 3 大 3 小 / 三区 2:2:2 / 一组二连 /
        重号 1 个）算的；一旦用户把 :class:`SelectionConfig` 改成别的形态，
        再拿这个数字去说事就是张冠李戴，所以这里返回 ``None``，文案也照实说。
        """
        config = self.config
        report = self.report
        if code == "odd_even":
            return report.odd_even_balanced if config.odd_count == BALANCED_COUNT else None
        if code == "big_small":
            return report.big_small_balanced if config.big_count == BALANCED_COUNT else None
        if code == "zone":
            expected: tuple[int, ...] = tuple(BALANCED_ZONES)
            return report.zone_balanced if tuple(config.zone_ratio) == expected else None
        if code == "run":
            if (config.consecutive_groups, config.max_run) != (1, ONE_PAIR_RUN):
                return None
            return report.one_pair_run
        if code == "repeat":
            if config.repeat_count != REPEAT_WITH_PREVIOUS:
                return None
            return report.repeat_with_previous
        return None

    # -- 统计画像 -----------------------------------------------------------

    def _deciles(self) -> tuple[int, int]:
        """历史和值的 10 分位与 90 分位。"""
        if not self._sums:
            return (0, 0)
        last = len(self._sums) - 1
        return (self._sums[int(0.1 * last)], self._sums[int(0.9 * last)])

    def _portraits(self, reds: Sequence[int], blue: int) -> tuple[Portrait, ...]:
        total = sum(reds)
        percentile = (
            bisect_right(self._sums, total) / len(self._sums) if self._sums else 0.0
        )
        low, high = self._sum_range
        blue_index = self.report.blue[blue].index
        occupied = f"{len(self._by_reds):,}".replace(",", " ")
        space = f"{RED_COMBINATIONS:,}".replace(",", " ")
        return (
            Portrait(
                "和值",
                str(total),
                f"历史 {len(self._sums)} 期均值 {self._sum_mean:.1f}，"
                f"10~90 分位 {low}~{high}；历史上有 {percentile:.0%} 的期数和值不超过它",
            ),
            Portrait("跨度", str(max(reds) - min(reds)), f"历史中位数 {self._span_median:.0f}"),
            Portrait(
                "蓝球",
                f"{blue:02d}",
                f"拥挤指数 {blue_index:.3f}，16 个蓝球里第 "
                f"{self.strategy.blue_rank(blue)} 冷（越冷越不容易被人分摊）",
            ),
            Portrait(
                "历史占位",
                f"{occupied} 组",
                f"历史 {self.history_periods} 期已占红球组合 {occupied} / {space} "
                f"= {len(self._by_reds) / RED_COMBINATIONS:.2%}，"
                "撞上完全相同的红球组合概率很低",
            ),
        )

    # -- 最小改动建议 -------------------------------------------------------

    def _suggest(self, reds: Sequence[int], blue: int) -> tuple[Suggestion, ...]:
        """单号替换就能满足全部条件（且不与历史重复）的改法，按和值改动升序。

        只试「换一个红球」：6 个位置 × 最多 27 个不在本注里的候选 ≈ 162 次判定，
        毫秒级。候选必须用 :meth:`SelectionStrategy.accepts` 判定，因此建议出来的
        号码与「开始选号」抽出来的号码遵循同一套规则。

        形态条件全满足、且不撞历史时直接返回空——没病就不开药。
        """
        if not self.strategy.shape_failures(reds) and not self.duplicates_history_for(reds):
            return ()

        total = sum(reds)
        found: list[Suggestion] = []
        for position, original in enumerate(reds):
            for candidate in RED_NUMBERS:
                if candidate in reds:
                    continue
                trial = list(reds)
                trial[position] = candidate
                trial_reds = tuple(sorted(trial))
                if not self.strategy.accepts(trial_reds):
                    continue
                found.append(
                    Suggestion(
                        replace=original,
                        with_number=candidate,
                        reds=trial_reds,
                        blue=blue,
                    )
                )
        found.sort(key=lambda item: (abs(sum(item.reds) - total), sum(item.reds),
                                     item.replace, item.with_number))
        return tuple(found)

    def duplicates_history_for(self, reds: Sequence[int]) -> bool:
        """红球是否与历史某一期完全相同（供内部与外部复用）。"""
        return frozenset(reds) in self._by_reds


def render_report(check: TicketCheck) -> list[str]:
    """把校验结果渲染成逐行文本（界面日志与测试共用同一份文案）。"""
    conditions = check.conditions
    lines = [f"校验号码：{check.label}"]

    if check.balanced:
        lines.append(f"  形态条件：{len(conditions)} 条全部满足（标准与「开始选号」同一套）")
    else:
        missing = "、".join(condition.label for condition in check.failed_conditions)
        passed = len(conditions) - len(check.failed_conditions)
        lines.append(f"  形态条件：{passed}/{len(conditions)} 条满足，不符合的是「{missing}」")
    lines.extend(condition.line for condition in conditions)

    lines.append("  统计画像")
    lines.extend(portrait.line for portrait in check.portraits)

    lines.append("  历史比对")
    if check.red_matches:
        periods = "、".join(match.label for match in check.red_matches)
        lines.append(f"    · 红球 6 个与历史完全相同：{periods}")
    else:
        lines.append("    · 红球 6 个与历史完全相同：无")
    if check.exact_matches:
        periods = "、".join(match.label for match in check.exact_matches)
        lines.append(f"    · 红球 + 蓝球都与历史完全相同：{periods}（历史开出过一模一样的号码）")
        if check.safe_blues:
            lines.append(f"    · 只改蓝球就能避开重复：换成 {_join(check.safe_blues)} 里的任意一个")
    else:
        lines.append("    · 红球 + 蓝球都与历史完全相同：无")

    if check.suggestions:
        preview = min(SUGGESTION_PREVIEW, len(check.suggestions))
        lines.append(
            f"  最小改动建议：单号替换即可满足全部形态条件的改法共 {len(check.suggestions)} 种，"
            f"按和值改动从小到大列前 {preview} 种"
        )
        lines.extend(suggestion.line for suggestion in check.suggestions[:preview])
    elif check.balanced and not check.red_matches:
        lines.append("  最小改动建议：不需要——形态条件已全部满足，且不与历史重复")

    probability = f"{TOTAL_COMBINATIONS:,}".replace(",", " ")
    lines.append(
        "  提醒：形态只决定「是不是按你认可的样式出号」，不提高中奖概率——"
        f"每注中奖概率恒为 1/{probability}，整体期望仍为负。请理性购彩。"
    )
    return lines
