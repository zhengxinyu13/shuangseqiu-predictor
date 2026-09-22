"""双色球选号系统 —— 桌面界面（Tkinter）。

两个按钮：

- **检查更新**：抓 ``55128.cn`` 与官方 ``cwl.gov.cn``，两者逐列一致才写进
  ``data/双色球历史开奖数据_全量.xlsx``；对不上就拒绝写入并报出是第几期。
- **开始选号**：按拥挤度分析的结论抽一注（红球偏 32/33 与大和值、避开 3 连号、
  蓝球按实测冷热度加权），并排除与历史红球完全相同的组合。

窗口下方的「运行日志」按时间戳记录全过程：抓取进度、交叉校验结论、写盘、耗时。
日志与按钮状态走**两条独立队列**（``updates`` / ``pending``），原因是进度行随时可能
到达，若和最终结果挤在同一条队列里，中途来一行就会把按钮提前解锁。

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
    r"%USERPROFILE%\.workbuddy\binaries\python\envs\ssq-picker\Scripts\python.exe"
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
from shuangseqiu.data import DATA_FILE_NAME, DEFAULT_DATA_DIR

TITLE = "双色球选号系统"
BG = "#F5F7FA"
CARD = "#FFFFFF"
INK = "#1F2933"
MUTED = "#6B7280"
RED_BALL = "#D32F2F"
BLUE_BALL = "#1565C0"
OK_GREEN = "#1B7F3B"
WARN_AMBER = "#B4690E"
ERR_RED = "#C0392B"

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 10, "bold")
FONT_BALL = ("Microsoft YaHei UI", 17, "bold")
FONT_LOG = ("Consolas", 9)
FONT_MONO = ("Consolas", 11, "bold")


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
        self.rowconfigure(3, weight=1)

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
            font=("Consolas", 8), anchor="w", padx=12,
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
            font=FONT_UI, anchor="w", padx=12, justify="left", wraplength=620,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 10))

        # 日志
        log_card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground="#E3E8EF")
        log_card.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)
        tk.Label(
            log_card, text="运行日志", bg=CARD, fg=INK, font=FONT_SECTION, anchor="w", padx=12,
        ).grid(row=0, column=0, sticky="w", pady=(9, 4))
        self.log = tk.Text(
            log_card, height=11, font=FONT_LOG, bg="#FBFCFE", fg=INK, relief="flat",
            wrap="word", padx=10, pady=8,
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log.configure(state="disabled")
        for tag, colour in (("ok", OK_GREEN), ("warn", WARN_AMBER), ("err", ERR_RED), ("dim", MUTED)):
            self.log.tag_configure(tag, foreground=colour)

    # -- 基础动作 ----------------------------------------------------------

    def _log(self, text: str, tag: str | None = None) -> None:
        """写日志。多行文本只给第一行打时间戳，后续行跟着缩进对齐。"""
        stamp = f"[{time.strftime('%H:%M:%S')}] "
        self.log.configure(state="normal")
        for index, line in enumerate(text.splitlines() or [""]):
            self.log.insert("end", (stamp if index == 0 else "") + line + "\n", tag or "")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _progress(self, text: str) -> None:
        """进程回调：把一行进展投递给界面线程。

        由后台线程（含抓取线程）调用，所以只往队列里塞字符串——
        Tkinter 的控件只能在主线程碰。
        """
        self.updates.put(("dim", f"  {text}"))

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
        self._log(f"—— {title} ——", "dim")
        if subtitle:
            self._log(subtitle, "dim")

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
            self._progress("按拥挤度加权抽样（拒绝采样，不满足条件就丢弃重抽）…")
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
        self._log(f"已复制到剪贴板：{self.last_selection.label}", "dim")

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
            f"和值 {selection.sum}　尝试 {selection.attempts} 次　"
            f"因撞历史重抽 {selection.history_hits} 次　"
            + ("（注意：本次未完全满足过滤条件，建议重抽）" if selection.relaxed else "已排除与历史重复的红球组合")
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="双色球选号系统")
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_DIR / DATA_FILE_NAME,
        help="历史开奖数据工作簿路径",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = tk.Tk()
    root.title(TITLE)
    root.geometry("760x680")
    root.minsize(680, 560)
    root.configure(bg=BG)
    ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    app = PickerApp(root, args.data)
    app._log(f"数据文件：{args.data}", "dim")
    if not args.data.is_file():
        app._log(f"找不到数据文件：{args.data}", "err")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
