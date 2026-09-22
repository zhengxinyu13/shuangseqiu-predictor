"""「检查更新」：抓最新开奖，双向校验通过才写进工作簿。

两个来源，各司其职：

=================  ==============================================  ==============
来源               说明                                            角色
=================  ==============================================  ==============
55128.cn           用户指定的站点，不带参数即返回最新 80 期        用户请求源
cwl.gov.cn         中国福利彩票官方开奖公告 API，2013 年起全量      权威仲裁源
=================  ==============================================  ==============

**为什么必须双向**：``55128.cn`` 在 2003–2004 年存在缺期与号码错误
（明细见 ``data/.workbuddy/memory/MEMORY.md``）。它的近期数据经实测与官方一致，
可以作为增量来源，但不能单独采信，所以每一期新号码都要求两个来源完全吻合
（红球、蓝球、开奖日期），并对**重叠期**回头比对本地库，防止某个源偷偷改历史数据。
任何一处对不上就**拒绝写盘**，把具体是第几期、差在哪一列报出来，绝不将就写入。

额外的收获：官方接口顺带给出销售额、奖池与各奖级中奖注数，
正好补上「奖金与奖池」表——所以新增的一期是**完整**的，不是缺字段的半条记录。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from .dataset import DrawRecord, build_checks, read_records, write_records

SOURCE_PRIMARY = "55128.cn"
SOURCE_OFFICIAL = "cwl.gov.cn"
SOURCE_NAMES: tuple[str, ...] = (SOURCE_PRIMARY, SOURCE_OFFICIAL)

URL_55128 = "https://www.55128.cn/kjh/fcssq-history-80.htm"
URL_OFFICIAL = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 30.0

Progress = Callable[[str], None]
"""进度回调：接收一行人类可读的进展文本。界面用它把过程写进日志。"""

# 55128.cn 列表页：行 / 单元格 / 号码 span / 日期 / 期号
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_BALL = re.compile(r'class="ball-list\s+(red|kjhblue)">\s*(\d{2})\s*</span>')
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISSUE = re.compile(r"^\d{7}$")
_TAG = re.compile(r"<[^>]+>")

# 官方接口奖级编号
_GRADE_FIRST = 1
_GRADE_SECOND = 2


class UpdateError(RuntimeError):
    """抓取或解析失败（网络、站点改版、接口报错等）。"""


@dataclass(frozen=True)
class RemoteDraw:
    """来自外部数据源的一期记录；派奖字段可能为空。"""

    issue: int
    date: date
    reds: tuple[int, ...]
    blue: int
    sales: int | None = None
    pool: int | None = None
    first_winners: int | None = None
    first_prize: int | None = None
    second_winners: int | None = None
    second_prize: int | None = None

    @property
    def label(self) -> str:
        return f"{self.issue}期"

    def to_record(self) -> DrawRecord:
        return DrawRecord(
            issue=self.issue,
            date=self.date,
            reds=self.reds,
            blue=self.blue,
            sales=self.sales,
            pool=self.pool,
            first_winners=self.first_winners,
            first_prize=self.first_prize,
            second_winners=self.second_winners,
            second_prize=self.second_prize,
        )


# --------------------------------------------------------------------------
# 解析（纯函数，便于离线测试）
# --------------------------------------------------------------------------

def _text(fragment: str) -> str:
    """剥标签取纯文本。"""
    return _TAG.sub("", fragment).replace("\xa0", " ").strip()


def _to_int(value: Any) -> int | None:
    """把接口里的字符串数字转成 int，空串 / 空值返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_55128(html: str) -> list[RemoteDraw]:
    """解析 55128.cn 开奖列表页。

    号码 span 的 class 形如 ``ball-list red`` / ``ball-list kjhblue``；
    日期与期号按内容特征定位（``YYYY-MM-DD`` / 7 位数字），不依赖列下标——
    该站后期插过「开机号」列，按下标取会整体错位。

    Args:
        html: 列表页 HTML。

    Returns:
        按页面顺序（最新在前）的记录。

    Raises:
        UpdateError: 页面能下载但一行都没解析出来（通常意味着站点改版）。
    """
    rows = _ROW.findall(html)
    if not rows:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)

    out: list[RemoteDraw] = []
    for row in rows:
        balls = _BALL.findall(row)
        if len(balls) != 7:
            continue
        reds = tuple(sorted(int(value) for kind, value in balls if kind == "red"))
        blues = [int(value) for kind, value in balls if kind == "kjhblue"]
        if len(reds) != 6 or len(blues) != 1:
            continue

        cells = [text for text in (_text(cell) for cell in _CELL.findall(row)) if text]
        day = next((text for text in cells if _DATE.match(text)), "")
        issue = next((text for text in cells if _ISSUE.match(text)), "")
        if not day or not issue:
            continue

        out.append(
            RemoteDraw(
                issue=int(issue),
                date=datetime.strptime(day, "%Y-%m-%d").date(),
                reds=reds,
                blue=blues[0],
            )
        )

    if not out:
        raise UpdateError(
            "55128.cn 页面能打开，但一行开奖记录都没解析出来——站点结构可能已改版，"
            "需要人工检查选择器。"
        )
    return out


def parse_official(payload: Mapping[str, Any]) -> list[RemoteDraw]:
    """解析官方 ``findDrawNotice`` 接口返回的 JSON。

    派奖字段来自 ``prizegrades``：``type`` 1 是一等奖、2 是二等奖，
    ``typenum`` 中奖注数、``typemoney`` 单注奖金。

    Raises:
        UpdateError: 接口返回 ``state != 0``（查询失败）。
    """
    state = payload.get("state")
    if str(state) not in {"0"}:
        raise UpdateError(f"官方接口返回失败：state={state!r} message={payload.get('message')!r}")

    out: list[RemoteDraw] = []
    for item in payload.get("result") or []:
        grades: dict[int, Mapping[str, Any]] = {}
        for grade in item.get("prizegrades") or []:
            key = _to_int(grade.get("type"))
            if key is not None:
                grades[key] = grade
        first = grades.get(_GRADE_FIRST, {})
        second = grades.get(_GRADE_SECOND, {})

        out.append(
            RemoteDraw(
                issue=int(item["code"]),
                date=datetime.strptime(str(item["date"])[:10], "%Y-%m-%d").date(),
                reds=tuple(sorted(int(v) for v in str(item["red"]).split(","))),
                blue=int(item["blue"]),
                sales=_to_int(item.get("sales")),
                pool=_to_int(item.get("poolmoney")),
                first_winners=_to_int(first.get("typenum")),
                first_prize=_to_int(first.get("typemoney")),
                second_winners=_to_int(second.get("typenum")),
                second_prize=_to_int(second.get("typemoney")),
            )
        )

    if not out:
        raise UpdateError("官方接口没有返回任何开奖记录。")
    return out


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

def _client(referer: str) -> httpx.Client:
    """构造带桌面 UA 与 Referer 的客户端。

    部分彩票站点证书链不全，故关闭证书校验；这里只读公开数据，不接受用户输入。
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Referer": referer},
        timeout=TIMEOUT,
        follow_redirects=True,
        verify=False,
    )


def fetch_55128(url: str = URL_55128, client: httpx.Client | None = None) -> list[RemoteDraw]:
    """抓取并解析 55128.cn 最新开奖列表（不带参数即最新 80 期）。

    Raises:
        UpdateError: 网络异常、HTTP 非 2xx 或页面结构无法解析时。
    """
    try:
        if client is not None:
            response = client.get(url)
        else:
            with _client(url) as owned:
                response = owned.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise UpdateError(f"访问 55128.cn 失败：{error}") from error
    return parse_55128(response.content.decode("utf-8", "ignore"))


def fetch_official(count: int = 100, client: httpx.Client | None = None) -> list[RemoteDraw]:
    """抓取官方开奖公告接口（2013 年起全量）。

    Raises:
        UpdateError: 网络异常、HTTP 非 2xx 或接口报错时。
    """
    params = {
        "name": "ssq",
        "issueStart": "",
        "issueEnd": "",
        "issueCount": "",
        "dayStart": "",
        "dayEnd": "",
        "pageNo": 1,
        "pageSize": count,
    }
    try:
        if client is not None:
            response = client.get(URL_OFFICIAL, params=params)
        else:
            with _client("https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/") as owned:
                response = owned.get(URL_OFFICIAL, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        raise UpdateError(f"访问官方接口失败：{error}") from error
    except ValueError as error:
        raise UpdateError(f"官方接口返回的不是合法 JSON：{error}") from error
    return parse_official(payload)


def _fetch_sources(
    loaders: Mapping[str, Callable[[], Sequence[RemoteDraw]]],
    notify: Progress,
) -> tuple[dict[str, Sequence[RemoteDraw]], list[str]]:
    """并行抓取全部来源。

    两个站点互不依赖，串行只会把耗时相加（实测 0.65 秒 + 0.45 秒）。并行之后总耗时
    取决于最慢的那个；更关键的是某个站点卡到 30 秒超时时，另一个不会被拖着一起等。

    Args:
        loaders: ``来源名 -> 抓取函数``。
        notify: 每抓完一个来源回调一次；由抓取线程调用，实现需自带线程安全。

    Returns:
        ``(成功结果, 错误信息)``。错误按 :data:`SOURCE_NAMES` 顺序排列，便于稳定断言。
    """
    fetched: dict[str, Sequence[RemoteDraw]] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(SOURCE_NAMES), thread_name_prefix="ssq-fetch") as pool:
        futures = {pool.submit(loaders[name]): name for name in SOURCE_NAMES}
        for future in as_completed(futures):
            name = futures[future]
            try:
                rows = future.result()
            except UpdateError as error:
                errors[name] = f"抓取 {name} 失败：{error}"
                continue
            except Exception as error:  # 网络/解析之外的其他意外，也要变成可读提示
                errors[name] = f"抓取 {name} 时出现意外错误：{error!r}"
                continue
            fetched[name] = rows
            notify(
                f"{name}：抓到 {len(rows)} 期，最新 {max(r.issue for r in rows)}期"
                if rows
                else f"{name}：未取到数据"
            )

    return fetched, [errors[name] for name in SOURCE_NAMES if name in errors]


# --------------------------------------------------------------------------
# 比对与计划
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdatePlan:
    """一次「检查更新」的结论。"""

    local_latest: int
    local_total: int
    remote_latest: int | None
    new_records: tuple[DrawRecord, ...]
    conflicts: tuple[str, ...]
    check_rows: tuple[tuple[Any, ...], ...]
    source_rows: tuple[tuple[str, int, int], ...] = field(default=())

    @property
    def has_new(self) -> bool:
        """是否有比本地更新的期号。"""
        return bool(self.new_records)

    @property
    def can_write(self) -> bool:
        """是否可以安全写盘（没有冲突）。"""
        return not self.conflicts


def _describe(draw: RemoteDraw | DrawRecord) -> str:
    reds = " ".join(f"{n:02d}" for n in sorted(draw.reds))
    return f"{reds} +{draw.blue:02d}"


def _compare(issue: int, left: RemoteDraw | DrawRecord, right: RemoteDraw | DrawRecord) -> list[str]:
    """逐列比对两期记录，返回差异描述。"""
    problems: list[str] = []
    if sorted(left.reds) != sorted(right.reds):
        problems.append(f"红球不同（{_describe(left)} vs {_describe(right)}）")
    if left.blue != right.blue:
        problems.append(f"蓝球不同（{left.blue:02d} vs {right.blue:02d}）")
    if left.date != right.date:
        problems.append(f"开奖日期不同（{left.date} vs {right.date}）")
    return problems


def build_update_plan(
    local: Sequence[DrawRecord],
    sources: Mapping[str, Sequence[RemoteDraw]],
) -> UpdatePlan:
    """比对本地库与各外部来源，产出更新计划。

    规则：

    1. 新期号（大于本地最新期号）必须**同时**出现在两个来源里，且号码与日期完全一致；
    2. 重叠期（两个来源里本地已有的期号）回头与本地库比对，防止来源改动历史数据；
    3. 任何不一致都记进 ``conflicts``，此时 ``can_write`` 为假，调用方不得写盘。

    Args:
        local: 本地全部记录（顺序无关）。
        sources: ``来源名 -> 记录``，需含 :data:`SOURCE_NAMES` 两个键。

    Returns:
        :class:`UpdatePlan`。
    """
    missing = [name for name in SOURCE_NAMES if name not in sources]
    if missing:
        raise ValueError(f"缺少数据源：{missing!r}")

    indexed: dict[str, dict[int, RemoteDraw]] = {
        name: {draw.issue: draw for draw in sources[name]} for name in SOURCE_NAMES
    }
    local_by_issue = {record.issue: record for record in local}
    local_latest = max(local_by_issue)
    primary, official = indexed[SOURCE_PRIMARY], indexed[SOURCE_OFFICIAL]

    conflicts: list[str] = []
    source_rows: list[tuple[str, int, int]] = []
    check_rows: list[tuple[Any, ...]] = []

    for name in SOURCE_NAMES:
        draws = indexed[name]
        source_rows.append((name, len(draws), max(draws) if draws else 0))
    remote_latest = max((max(draws) for draws in indexed.values() if draws), default=None)

    # --- 新期号：双源一致才采信 ---
    new_issues = sorted(
        issue for name in SOURCE_NAMES for issue in indexed[name] if issue > local_latest
    )
    new_issues = sorted(set(new_issues))
    new_records: list[DrawRecord] = []
    for issue in new_issues:
        in_primary = primary.get(issue)
        in_official = official.get(issue)
        if in_primary is None:
            conflicts.append(f"{issue}期：官方已发布，但 55128.cn 未收录，拒绝写入")
            continue
        if in_official is None:
            conflicts.append(
                f"{issue}期：55128.cn 已收录，但官方接口未返回（官方仅覆盖 2013 年起），拒绝写入"
            )
            continue
        differences = _compare(issue, in_primary, in_official)
        if differences:
            conflicts.append(f"{issue}期：两个来源互相矛盾——" + "；".join(differences))
            continue
        # 双源一致，采用官方记录（带销售额与奖池，字段完整）
        new_records.append(in_official.to_record())

    # --- 重叠期：防止来源篡改历史 ---
    overlap_rows: list[tuple[Any, ...]] = []
    for name in SOURCE_NAMES:
        draws = indexed[name]
        shared = [issue for issue in draws if issue in local_by_issue]
        mismatched: list[str] = []
        for issue in sorted(shared):
            differences = _compare(issue, draws[issue], local_by_issue[issue])
            if differences:
                mismatched.append(f"{issue}期（" + "；".join(differences) + "）")
        conflicts.extend(f"{name} 历史数据与本地库冲突：{item}" for item in mismatched[:10])
        overlap_rows.append((name, len(shared), len(mismatched)))

    # --- 汇总进「数据校验」表 ---
    for name, periods, latest in source_rows:
        check_rows.append(
            ("数据来源", name, "通过" if periods else "警告",
             f"抓取 {periods} 期，最新 {latest}期" if periods else "未取到数据")
        )
    check_rows.append(
        ("交叉校验", "新期号双源一致", "通过" if not conflicts else "警告",
         "无新增期号" if not new_issues else
         (f"新增 {len(new_records)} 期，双源逐列一致" if not conflicts
          else f"发现 {len(conflicts)} 处冲突，已拒绝写入"))
    )
    for name, shared, mismatched in overlap_rows:
        check_rows.append(
            ("交叉校验", f"{name} 重叠期与本地库一致", "通过" if mismatched == 0 else "警告",
             f"重叠 {shared} 期，不一致 {mismatched} 期")
        )
    check_rows.append(("更新", "本次检查时间", "通过", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    check_rows.append(
        ("更新", "本地库状态", "通过",
         f"{len(local)} 期，最新 {local_latest}期" + (f" → {remote_latest}期" if new_records else "（已是最新）"))
    )

    return UpdatePlan(
        local_latest=local_latest,
        local_total=len(local),
        remote_latest=remote_latest,
        new_records=tuple(sorted(new_records, key=lambda r: r.issue)),
        conflicts=tuple(conflicts),
        check_rows=tuple(check_rows),
        source_rows=tuple(source_rows),
    )


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateResult:
    """「检查更新」的对外结果。"""

    status: str  # "updated" | "current" | "conflict" | "error"
    message: str
    plan: UpdatePlan | None = None
    written_path: Path | None = None
    total_records: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {"updated", "current"}


def apply_update(
    path: Path | str,
    plan: UpdatePlan,
    records: Sequence[DrawRecord] | None = None,
) -> tuple[Path, int]:
    """把计划里的新记录并入工作簿并重算全部派生表。

    Args:
        path: 工作簿路径。
        plan: :func:`build_update_plan` 的结果，且 ``can_write`` 必须为真。
        records: 已有的本地记录。调用方刚读过就直接传进来，省掉一次整表读盘；
            传 ``None`` 时自己读。注意：传进来的必须是**这份计划所依据的**那一版数据，
            否则合并结果会与校验结论对不上。

    Returns:
        ``(实际写入路径, 新总期数)``。

    Raises:
        ValueError: 计划含冲突时（调用方应先检查 ``can_write``）。
        OSError: 文件被占用无法替换时。
    """
    if plan.conflicts:
        raise ValueError(f"计划存在冲突，不能写盘：{plan.conflicts[:3]}")

    target = Path(path)
    current = list(records) if records is not None else read_records(target)
    merged = sorted(
        {record.issue: record for record in [*current, *plan.new_records]}.values(),
        key=lambda record: record.issue,
    )
    written = write_records(target, merged, build_checks(merged, plan.check_rows))
    return written, len(merged)


def check_and_update(
    path: Path | str,
    fetchers: Mapping[str, Callable[[], Sequence[RemoteDraw]]] | None = None,
    progress: Progress | None = None,
) -> UpdateResult:
    """检查更新：抓两个来源 → 校验 → 一致则写盘。

    Args:
        path: 工作簿路径。
        fetchers: ``来源名 -> 抓取函数``。默认走真实网络；
            测试里注入假函数即可完全离线。
        progress: 进度回调，每次收到一行人类可读的进展文本。界面用它把过程写进日志；
            测试里传 ``list.append`` 就能断言。回调会在后台线程（含抓取线程）被调用，
            实现必须自己保证线程安全。

    Returns:
        :class:`UpdateResult`。任何失败都通过 ``status`` 与 ``message`` 返回，不抛异常，
        以便界面直接展示。
    """
    target = Path(path)
    loaders = dict(fetchers or {SOURCE_PRIMARY: fetch_55128, SOURCE_OFFICIAL: fetch_official})
    notify: Progress = progress or (lambda _message: None)

    notify("读取本地工作簿…")
    try:
        local = read_records(target)
    except (OSError, ValueError) as error:
        return UpdateResult("error", f"读取本地数据失败：{error}")
    notify(f"本地 {len(local)} 期，最新 {local[-1].label}")

    for name in SOURCE_NAMES:
        if name not in loaders:
            return UpdateResult("error", f"未提供数据源 {name!r} 的抓取函数")

    notify(f"并行抓取 {SOURCE_PRIMARY} 与 {SOURCE_OFFICIAL}…")
    fetched, errors = _fetch_sources(loaders, notify)
    if errors:
        return UpdateResult("error", errors[0])

    notify("交叉校验：新期号须两源逐列一致，重叠期回头比对本地库…")
    plan = build_update_plan(local, fetched)

    if plan.conflicts:
        notify(f"发现 {len(plan.conflicts)} 处冲突，未写入任何数据")
        return UpdateResult(
            "conflict",
            "两个来源对不上，已拒绝写入：" + "；".join(plan.conflicts[:3])
            + (f"（共 {len(plan.conflicts)} 处）" if len(plan.conflicts) > 3 else ""),
            plan=plan,
            total_records=plan.local_total,
        )

    if not plan.has_new:
        # 没有新增期号也要回写一次：把「本次检查时间」等校验行刷新进去
        notify("无新增期号，仅刷新「数据校验」表…")
        try:
            written, total = apply_update(target, plan, local)
        except (OSError, ValueError) as error:
            return UpdateResult("error", f"刷新校验信息失败：{error}", plan=plan,
                                total_records=plan.local_total)
        notify(f"完成：本地 {total} 期已是最新")
        return UpdateResult(
            "current",
            f"已是最新，本地 {plan.local_total} 期，最新 {plan.local_latest}期。"
            f"（{SOURCE_PRIMARY} 与 {SOURCE_OFFICIAL} 已核对一致）",
            plan=plan,
            written_path=written,
            total_records=total,
        )

    notify(f"写入工作簿：新增 {len(plan.new_records)} 期，6 张表全部重算…")
    try:
        written, total = apply_update(target, plan, local)
    except OSError as error:
        return UpdateResult("error", str(error), plan=plan, total_records=plan.local_total)
    notify(f"完成：本地共 {total} 期")

    issues = "、".join(record.label for record in plan.new_records)
    return UpdateResult(
        "updated",
        f"已新增 {len(plan.new_records)} 期（{issues}），本地共 {total} 期，"
        f"最新 {plan.new_records[-1].label}。",
        plan=plan,
        written_path=written,
        total_records=total,
    )
