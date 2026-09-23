"""双色球选号系统 —— 桌面界面（Tkinter）。

两个按钮：

- **检查更新**：抓 ``55128.cn`` 与官方 ``cwl.gov.cn``，两者逐列一致才写进
  ``data/双色球历史开奖数据_全量.xlsx``；对不上就拒绝写入并报出是第几期。
- **开始选号**：按形态条件抽一注（红球 3 奇 3 偶 + 3 大 3 小 + 三区比 2:2:2 +
  恰好 1 组二连号 + 与上一期重号 1 个；蓝球仍按实测冷热度加权），
  并排除与历史红球完全相同的组合。

窗口下方的「运行日志」按时间戳记录全过程：抓取进度、交叉校验结论、写盘、耗时。
日志与按钮状态走**两条独立队列**（``updates`` / ``pending``），原因是进度行随时可能
到达，若和最终结果挤在同一条队列里，中途来一行就会把按钮提前解锁。

字号与配色集中在文件顶部的 ``FONT_*`` / ``LOG_*`` 常量里，要调就改那里。基准：

- 正文不小于 11pt，日志 11pt，主标题 18pt；
- **白底上不要用浅灰写正文**。日志正文近黑 ``#111827``、进度行 ``#334155``、
  时间戳 ``#64748B``；Grayson 2026-09-23 反馈过「灰字白底看着费眼睛」。

运行（**必须用带 tkinter 的解释器**，本机是 Python 3.14）::

    %USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\ssq-picker\\Scripts\\python.exe picker.py

可选参数 ``--data <xlsx 路径>`` 指定其他数据文件。

关于「选号」能做什么、不能做什么：**它不改变中奖概率**（每注都是 1/17 721 088），
只降低与别人撞号后分摊奖金的概率。三至六等奖是固定奖金，完全不受影响。
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:  # 支持从任意目录直接运行
    sys.path.insert(0, str(PROJECT_ROOT))

SUGGESTED_PYTHON = (
    Path.home() / ".workbuddy" / "binaries" / "python" / "envs"
    / "ssq-picker" / "Scripts" / "python.exe"
)

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError as error:  # pragma: no cover - 只在环境缺 tkinter 时触发
    raise SystemExit(
        "当前解释器没有 tkinter，无法启动界面。\n"
        f"请改用这个解释器运行：\n    {SUGGESTED_PYTHON} picker.py"
    ) from error

from shuangseqiu import dataset, selector, updater
from shuangseqiu.data import application_root, seed_default_data_file

TITLE = "双色球选号系统"
# 页脚署名（ASCII，不随语言环境变化），固定在窗口最底端居中。
FOOTER = "This software was written by Grayson Zheng."
# 窗口尺寸也集中在这里：截图工具要复用同一份，免得两处各写一个数字慢慢跑偏。
WIN_SIZE = (880, 840)
WIN_MIN_SIZE = (800, 720)
BG = "#F5F7FA"
CARD = "#FFFFFF"
INK = "#111827"
MUTED = "#4A5568"
RED_BALL = "#D32F2F"
BLUE_BALL = "#1565C0"

# 日志配色（2026-09-23 改）：原先正文用 #6B7280 浅灰 + 9pt 字体，
# 白底上整片发灰、看着费眼睛。现在正文用近黑，次要信息也压到足够深，
# 只有时间戳略浅但仍然清楚。
LOG_BG = "#F8FAFC"
LOG_INK = "#111827"
LOG_STEP = "#334155"   # 进度行：深石板灰，不再是浅灰
LOG_STAMP = "#64748B"  # 时间戳：看得出是附属信息，又不用眯眼
OK_GREEN = "#146C33"
WARN_AMBER = "#8A4B00"
ERR_RED = "#A3241A"

# 字号整体放大一档（Grayson 2026-09-23 反馈：界面字体偏小、看不清）
FONT_UI = ("Microsoft YaHei UI", 11)
FONT_TITLE = ("Microsoft YaHei UI", 18, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 12, "bold")
FONT_BALL = ("Microsoft YaHei UI", 20, "bold")
FONT_LOG = ("Consolas", 11)
FONT_LOG_HEAD = ("Microsoft YaHei UI", 11, "bold")
FONT_PATH = ("Consolas", 10)
# 页脚：与正文同档 11pt。这里刻意不缩小——署名再小也还是字，
# 「10pt 及以下不要用」这条规矩对页脚一样适用；靠 MUTED 颜色让它退到次要层级。
FONT_FOOTER = ("Microsoft YaHei UI", 11)


class PickerApp(ttk.Frame):
    """主窗口。网络与写盘都丢到后台线程，界面不卡。"""

    def __init__(self, master: tk.Tk, data_path: Path) -> None:
        super().__init__(master, padding=14)
        self.data_path = data_path
        self.pending: queue.Queue[tuple[Callable, object]] = queue.Queue()
        self.updates: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.last_selection: selector.Selection | None = None

        self._build_widgets()
        self._refresh_status()
        self.after(120, self._drain)

    # -- 界面搭建 ----------------------------------------------------------

    def _build_widgets(self) -> None:
        self.pack(fill="both", expand=True)
        self.columnconfigure(0, weight=1)
        # 富余高度全部给日志区（row 4）。号码卡片只占它的自然高度即可，
        # 否则 800px 高的窗口里它会撑出一大片空白，而日志只能挤出几行。
        self.rowconfigure(4, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=TITLE, font=FONT_TITLE, foreground=INK).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="不改变中奖概率，只降低撞号分摊的风险；请理性购彩。",
            font=FONT_UI,
            foreground=MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        # 数据状态卡片
        status_card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground="#E3E8EF")
        status_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        status_card.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="正在读取数据…")
        tk.Label(
            status_card, textvariable=self.status_var, bg=CARD, fg=INK,
            font=FONT_UI, anchor="w", justify="left", padx=12, pady=9,
        ).grid(row=0, column=0, sticky="ew")
        self.path_var = tk.StringVar(value=str(self.data_path))
        tk.Label(
            status_card, textvariable=self.path_var, bg=CARD, fg=MUTED,
            font=FONT_PATH, anchor="w", padx=12,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 9))

        # 按钮排
        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.update_button = ttk.Button(buttons, text="检查更新", command=self.on_check_update)
        self.update_button.pack(side="left", ipadx=14, ipady=5)
        self.select_button = ttk.Button(buttons, text="开始选号", command=self.on_select)
        self.select_button.pack(side="left", padx=(10, 0), ipadx=14, ipady=5)
        self.copy_button = ttk.Button(buttons, text="复制号码", command=self.on_copy, state="disabled")
        self.copy_button.pack(side="left", padx=(10, 0), ipadx=8, ipady=5)
        self.progress = ttk.Progressbar(buttons, mode="indeterminate", length=150)
        self.progress.pack(side="right")

        # 号码展示
        result_card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground="#E3E8EF")
        result_card.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        result_card.columnconfigure(0, weight=1)
        tk.Label(
            result_card, text="选号结果", bg=CARD, fg=INK, font=FONT_SECTION, anchor="w", padx=12,
        ).grid(row=0, column=0, sticky="w", pady=(9, 0))
        self.balls_frame = tk.Frame(result_card, bg=CARD, padx=12, pady=12)
        self.balls_frame.grid(row=1, column=0, sticky="w")
        self.hint_var = tk.StringVar(value="点「开始选号」抽一注。")
        tk.Label(
            result_card, textvariable=self.hint_var, bg=CARD, fg=MUTED,
            font=FONT_UI, anchor="w", padx=12, justify="left", wraplength=780,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 10))

        # 日志
        log_card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground="#E3E8EF")
        log_card.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        tk.Label(
            log_card, text="运行日志", bg=CARD, fg=INK, font=FONT_SECTION, anchor="w", padx=12,
        ).grid(row=0, column=0, sticky="w", pady=(9, 4))
        self.log = tk.Text(
            log_card, height=11, font=FONT_LOG, bg=LOG_BG, fg=LOG_INK, relief="flat",
            wrap="word", padx=12, pady=10,
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log.configure(state="disabled")
        # 日志标签：正文（无标签）用近黑；进度行与时间戳各自有专属颜色
        self.log.tag_configure("step", foreground=LOG_STEP)
        self.log.tag_configure("stamp", foreground=LOG_STAMP)
        self.log.tag_configure("head", foreground=INK, font=FONT_LOG_HEAD)
        self.log.tag_configure("ok", foreground=OK_GREEN)
        self.log.tag_configure("warn", foreground=WARN_AMBER)
        self.log.tag_configure("err", foreground=ERR_RED)

        # 页脚署名：固定在最底端、水平居中。row 5/6 都不给 weight，
        # 富余高度仍然只归日志卡（row 4），页脚只占自己的自然高度——
        # 否则它会把日志区的高度抢走。
        # 间距凑成 6+2+8+24 = 40px，正好等于窗口比原来多出来的 40px，
        # 于是日志卡的高度一点没变（仍是 401px）。
        ttk.Separator(self, orient="horizontal").grid(
            row=5, column=0, sticky="ew", pady=(6, 0),
        )
        ttk.Label(
            self, text=FOOTER, font=FONT_FOOTER, foreground=MUTED, anchor="center",
        ).grid(row=6, column=0, sticky="ew", pady=(8, 0))

    # -- 基础动作 ----------------------------------------------------------

    def _log(self, text: str, tag: str | None = None) -> None:
        """写日志。多行文本只给第一行打时间戳，后续行跟着缩进对齐。"""
        self.log.configure(state="normal")
        for index, line in enumerate(text.splitlines() or [""]):
            if index == 0:
                self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] ", "stamp")
            self.log.insert("end", line + "\n", tag or "")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _progress(self, text: str) -> None:
        """进程回调：把一行进展投递给界面线程。

        由后台线程（含抓取线程）调用，所以只往队列里塞字符串——
        Tkinter 的控件只能在主线程碰。
        """
        self.updates.put(("step", f"  {text}"))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.update_button.configure(state=state)
        self.select_button.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _refresh_status(self) -> None:
        try:
            records = dataset.read_records(self.data_path)
        except (OSError, ValueError) as error:
            self.status_var.set(f"数据读取失败：{error}")
            return
        latest = records[-1]
        self.status_var.set(
            f"本地共 {len(records)} 期，最新 {latest.label}"
            f"（{latest.date} 开出 {' '.join(f'{n:02d}' for n in latest.reds)} +{latest.blue:02d}）"
        )

    def _run_async(
        self,
        work: Callable[[], tuple[str, str, object]],
        title: str,
        subtitle: str = "",
    ) -> None:
        """在后台线程执行 ``work``，完成后回主线程处理。

        注意：只有 ``work`` 的**最终返回值**会被投递回界面线程
        （由 :meth:`_handle` 统一收尾并解除忙碌态），因此 ``work`` 内部
        不要自行往 ``pending`` 里塞东西，否则会提前把按钮解锁。
        过程中的进展请走 :meth:`_progress`，那是另一条只写日志的通道。
        """
        if self.busy:
            return
        self._set_busy(True)
        self._log(f"—— {title} ——", "head")
        if subtitle:
            self._log(subtitle, "step")

        def runner() -> None:
            try:
                outcome = work()
            except Exception as error:  # 兜底，避免线程静默死掉
                outcome = ("err", f"出现未预期的错误：{error!r}", error)
            self.pending.put((self._handle, outcome))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        """把后台结果搬到界面线程执行。

        两条通道刻意分开：

        - ``pending`` 只放**最终结果**，处理完解除忙碌态；
        - ``updates`` 放过程日志，只写日志、不碰按钮状态。

        若把两者挤进同一条队列，中途任何一行进度都会顺带把按钮解锁，
        于是任务还在跑、按钮却变可点了。
        """
        while True:
            try:
                tag, text = self.updates.get_nowait()
            except queue.Empty:
                break
            self._log(text, tag)
        while True:
            try:
                handler, payload = self.pending.get_nowait()
            except queue.Empty:
                break
            handler(payload)
        self.after(120, self._drain)

    def _handle(self, outcome: tuple[str, str, object]) -> None:
        tag, message, extra = outcome
        self._log(message, tag)
        self._set_busy(False)
        self._refresh_status()
        if isinstance(extra, selector.Selection):
            self._render_selection(extra)

    # -- 检查更新 ----------------------------------------------------------

    def on_check_update(self) -> None:
        def work() -> tuple[str, str, object]:
            started = time.perf_counter()
            result = updater.check_and_update(self.data_path, progress=self._progress)
            elapsed = time.perf_counter() - started
            tag = {"updated": "ok", "current": "ok", "conflict": "warn", "error": "err"}[result.status]
            lines = [result.message]
            if result.plan is not None:
                for row in result.plan.check_rows:
                    if row[0] == "交叉校验":
                        lines.append(f"  · {row[1]}：{row[3]}")
            if result.status == "conflict":
                lines.append("  未写入任何数据——两个来源对不上时报出来比写进去更重要。")
            lines.append(f"  耗时 {elapsed:.2f} 秒")
            return (tag, "\n".join(lines), None)

        self._run_async(work, "检查更新")

    # -- 选号 --------------------------------------------------------------

    def on_select(self) -> None:
        def work() -> tuple[str, str, object]:
            self._progress("读取历史工作簿…")
            records = dataset.read_records(self.data_path)
            self._progress(f"{len(records)} 期历史：重算拥挤指数并构建历史比对库…")
            strategy = selector.SelectionStrategy.from_records(records)
            self._progress("按形态条件抽样（3奇3偶 / 3大3小 / 三区2:2:2 / 1组连号 / 重号1个）…")
            selection = strategy.select()
            lines = [f"选出：{selection.label}"]
            lines.extend(f"  · {note}" for note in selection.notes)
            return ("ok", "\n".join(lines), selection)

        self._run_async(work, "开始选号")

    def on_copy(self) -> None:
        if self.last_selection is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self.last_selection.label)
        self._log(f"已复制到剪贴板：{self.last_selection.label}", "step")

    def _render_selection(self, selection: selector.Selection) -> None:
        self.last_selection = selection
        self.copy_button.configure(state="normal")

        for child in self.balls_frame.winfo_children():
            child.destroy()

        for number in selection.reds:
            tk.Label(
                self.balls_frame, text=f"{number:02d}", bg=RED_BALL, fg="white",
                font=FONT_BALL, width=3, pady=6,
            ).pack(side="left", padx=(0, 7))
        tk.Label(self.balls_frame, text="+", bg=CARD, fg=MUTED, font=FONT_BALL).pack(side="left", padx=(2, 9))
        tk.Label(
            self.balls_frame, text=f"{selection.blue:02d}", bg=BLUE_BALL, fg="white",
            font=FONT_BALL, width=3, pady=6,
        ).pack(side="left")

        self.hint_var.set(
            f"奇偶 {dataset.odd_even_text(selection.reds)}　"
            f"大小 {dataset.big_small_text(selection.reds)}　"
            f"三区 {dataset.zone_text(selection.reds)}　"
            f"连号 {dataset.streak_text(selection.reds) or '无'}　"
            f"重号 {selection.repeat_count} 个　"
            f"和值 {selection.sum}　尝试 {selection.attempts} 次"
            + ("　（注意：本次未完全满足形态条件，建议重抽）" if selection.relaxed else "")
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="双色球选号系统")
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="历史开奖数据工作簿路径（默认取程序目录下 data/，首次运行自动释放）",
    )
    return parser.parse_args(argv)


def resolve_data_path(explicit: Path | None) -> Path:
    """决定用哪个数据文件。

    显式 ``--data`` 优先；否则用程序目录下的 ``data/``，
    打包成 exe 时分发版会在这里释放一份初始数据。
    """
    if explicit is not None:
        return explicit
    return seed_default_data_file()


def report_fatal_error(error: BaseException) -> None:
    """把启动期异常摆到用户面前。

    打包成 ``--windowed`` 的 exe 后**没有控制台**，异常直接抛出去的现象是
    「双击了，什么都没发生」——这是最难排查的失败方式，所以必须弹窗。
    连窗口都建不起来时（例如缺 tkinter）退回到往程序目录写日志文件。
    """
    import traceback

    detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    try:
        messagebox.showerror(f"{TITLE} 启动失败", f"{error}\n\n{detail[-1500:]}")
        return
    except Exception:  # noqa: BLE001 - 弹窗本身失败时只能退到写文件
        pass
    try:
        log = application_root() / "启动失败.log"
        log.write_text(detail, encoding="utf-8")
    except Exception:  # noqa: BLE001 - 连日志都写不了就无计可施了
        pass


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except Exception as error:  # noqa: BLE001 - 打包后没有控制台，必须自己兜住
        report_fatal_error(error)
        return 1


def _run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_path = resolve_data_path(args.data)
    root = tk.Tk()
    root.title(TITLE)
    # 840 而非 800：多出来的 40px 正好让给页脚署名，
    # 日志卡因此仍然保持原先实测的 401px（约 19 行可见），不会因为加页脚而被挤扁。
    root.geometry(f"{WIN_SIZE[0]}x{WIN_SIZE[1]}")
    root.minsize(*WIN_MIN_SIZE)
    root.configure(bg=BG)
    ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    app = PickerApp(root, data_path)
    app._log(f"数据文件：{data_path}", "step")
    if not data_path.is_file():
        app._log(f"找不到数据文件：{data_path}", "err")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
