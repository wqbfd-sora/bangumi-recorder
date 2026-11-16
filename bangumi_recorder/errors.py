"""报错与日志。

- data/logs/app.log    ：运行日志（启动信息、主要操作、警告和错误），自动轮换，最多约 3 MB
- data/logs/crash.log  ：程序真正崩溃（闪退）时的现场（faulthandler 写入，含所有线程的调用栈）
- 未处理的 Python 异常：写进日志，并弹出对话框（可以复制错误信息、打开日志文件夹）
- 上次异常退出：下次启动时提示，并附上崩溃记录
"""
import faulthandler
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__, config

log = logging.getLogger("bangumi_recorder")

LOG_DIR = config.DATA_DIR / "logs"
APP_LOG = LOG_DIR / "app.log"
CRASH_LOG = LOG_DIR / "crash.log"
RUNNING_FLAG = LOG_DIR / "running.flag"

_crash_file = None
_reporter = None


# ---------------- 日志 ----------------
class _StreamToLog:
    """打包成 exe（无控制台）时 sys.stdout/stderr 是 None，把输出转进日志。"""

    def __init__(self, level):
        self.level = level
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                log.log(self.level, line)

    def flush(self):
        pass


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(APP_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stdout is None:
        sys.stdout = _StreamToLog(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _StreamToLog(logging.ERROR)
    log.info("==== %s %s 启动 ====", APP_NAME, __version__)


def log_environment():
    try:
        import PySide6
        qt = PySide6.__version__
    except Exception:  # noqa: BLE001
        qt = "?"
    log.info("Python %s | PySide6 %s | %s | 打包版=%s | 数据=%s", platform.python_version(), qt,
             platform.platform(), config.FROZEN, config.DATA_DIR)


# ---------------- 崩溃现场 ----------------
def enable_crash_dump():
    """闪退（访问违规等原生崩溃）时，把所有线程的调用栈写进 crash.log。"""
    global _crash_file
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if CRASH_LOG.exists() and CRASH_LOG.stat().st_size > 1_000_000:   # 太大了只留最后一段
            tail = CRASH_LOG.read_bytes()[-200_000:]
            CRASH_LOG.write_bytes(tail)
        _crash_file = open(CRASH_LOG, "a", encoding="utf-8")       # noqa: SIM115  需要一直开着
        _crash_file.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 {APP_NAME} {__version__} "
                          f"(pid {os.getpid()}) ====\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        log.exception("无法打开 crash.log")


HANG_SECONDS = 15
_watchdog_timer = None


def start_hang_watchdog():
    """界面卡住（未响应）超过 HANG_SECONDS 秒时，把所有线程当时在做什么写进 crash.log。

    原理：主线程每 2 秒「续一次命」；主线程卡住就续不上，faulthandler 的看门狗线程（不需要 Python 锁）
    到点后自动把现场写下来。程序不会因此退出。
    """
    global _watchdog_timer
    if not _crash_file:
        return
    from PySide6.QtCore import QTimer

    def rearm():
        try:
            faulthandler.dump_traceback_later(HANG_SECONDS, repeat=False, file=_crash_file, exit=False)
        except (ValueError, OSError):
            pass

    _watchdog_timer = QTimer()
    _watchdog_timer.timeout.connect(rearm)
    _watchdog_timer.start(2000)
    rearm()


def previous_run_crashed():
    return RUNNING_FLAG.exists()


def mark_running():
    try:
        RUNNING_FLAG.write_text(f"{os.getpid()} {datetime.now().isoformat()}", encoding="utf-8")
    except OSError:
        pass


def mark_clean_exit():
    try:
        RUNNING_FLAG.unlink(missing_ok=True)
    except OSError:
        pass
    log.info("==== 正常退出 ====")
    if _crash_file:
        try:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
            _crash_file.write(f"---- {datetime.now():%H:%M:%S} 正常退出 ----\n")
            _crash_file.close()
        except (OSError, ValueError):
            pass


def last_crash_section(max_chars=6000):
    """crash.log 里最近一次运行的记录（如果那次有崩溃现场的话）。"""
    try:
        text = CRASH_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    sections = text.split("\n==== ")
    # 最后一段是本次启动；倒数第二段是上次运行
    prev = sections[-2] if len(sections) >= 2 else ""
    body = prev.split("\n", 1)[1] if "\n" in prev else ""
    if not body.strip() or "正常退出" in body:
        return ""
    return ("==== " + prev)[-max_chars:].replace("Timeout (", f"界面卡住超过 {HANG_SECONDS} 秒时的现场 Timeout (")


def tail_app_log(lines=60):
    try:
        with open(APP_LOG, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


# ---------------- 异常 → 日志 + 对话框 ----------------
def _format(exc_type, exc, tb):
    return "".join(traceback.format_exception(exc_type, exc, tb))


def install_hooks():
    """接管所有未处理的异常和 Qt 警告。在 QApplication 创建之后调用。"""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        text = _format(exc_type, exc, tb)
        log.error("未处理的异常：\n%s", text)
        report_error("程序出错了", f"{exc_type.__name__}: {exc}", text)

    def thread_hook(args):
        text = _format(args.exc_type, args.exc_value, args.exc_traceback)
        log.error("线程 %s 里未处理的异常：\n%s", getattr(args.thread, "name", "?"), text)
        report_error("后台任务出错了", f"{args.exc_type.__name__}: {args.exc_value}", text)

    sys.excepthook = excepthook
    threading.excepthook = thread_hook

    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qt_log = logging.getLogger("qt")

    def qt_handler(mode, context, message):
        # 无关紧要的平台提示不记
        if "propagateSizeHints" in message:
            return
        qt_log.log(levels.get(mode, logging.WARNING), message)
        if mode == QtMsgType.QtFatalMsg and _crash_file:
            _crash_file.write(f"Qt 致命错误：{message}\n")
            _crash_file.flush()

    qInstallMessageHandler(qt_handler)


def report_error(title, summary, details):
    """任何线程都可以调用：在主线程弹出错误对话框（同一错误短时间内只弹一次）。"""
    if _reporter is not None:
        _reporter.request.emit(title, summary, details)


def make_reporter(parent_getter):
    """创建主线程里的错误弹窗器。parent_getter() 返回当前主窗口（可能为 None）。"""
    global _reporter
    from PySide6.QtCore import QObject, Qt, Signal

    class _Reporter(QObject):
        request = Signal(str, str, str)

        def __init__(self):
            super().__init__()
            self.showing = False
            self.recent = {}
            # 排队到当前事件处理完再弹：如果是在绘制界面时出的错，当场弹模态框会让界面卡住
            self.request.connect(self._show, Qt.QueuedConnection)

        def _show(self, title, summary, details):
            now = time.monotonic()
            if self.showing or now - self.recent.get(summary, -999) < 10:
                return                      # 同一个错误 10 秒内不重复弹；正在弹着也不叠加
            self.recent[summary] = now
            self.showing = True
            try:
                show_error_dialog(parent_getter(), title, summary, details)
            finally:
                self.showing = False

    _reporter = _Reporter()
    return _reporter


def open_log_folder():
    path = str(LOG_DIR)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _env_text():
    return (f"{APP_NAME} {__version__} | Python {platform.python_version()} | {platform.platform()} | "
            f"打包版={config.FROZEN}")


def show_error_dialog(parent, title, summary, details, intro=None):
    from PySide6.QtWidgets import QApplication, QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(intro or "操作没有完成，但你的数据是安全的（重要操作前都有自动备份）。")
    box.setInformativeText(f"{summary}\n\n详细信息已记录在 data\\logs\\app.log。"
                           "需要帮忙排查时，点「复制错误信息」发给 Claude。")
    full = f"{_env_text()}\n\n{details}"
    box.setDetailedText(full)
    copy_btn = box.addButton("复制错误信息", QMessageBox.ActionRole)
    open_btn = box.addButton("打开日志文件夹", QMessageBox.ActionRole)
    box.addButton("确定", QMessageBox.AcceptRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is copy_btn:
        QApplication.clipboard().setText(full)
    elif clicked is open_btn:
        open_log_folder()


def show_crash_notice(parent):
    """上次没有正常退出时提示。"""
    crash = last_crash_section()
    recent = tail_app_log(40)
    details = (crash or "（没有捕捉到崩溃现场，可能是被强制结束或断电）") + \
        "\n\n---- 上次运行的最后几行日志 ----\n" + recent
    log.warning("检测到上次异常退出")
    show_error_dialog(parent, "上次程序意外退出了", "上次运行没有正常关闭。", details,
                      intro="上次程序意外退出了（闪退）。崩溃时的情况已经记录下来。")
