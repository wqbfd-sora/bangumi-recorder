"""程序入口。"""
import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, config, errors

log = logging.getLogger("bangumi_recorder")


def _windows_app_id():
    """让任务栏显示本程序的图标，而不是 Python 的图标。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WqbfdSora.BangumiRecorder")
        except (AttributeError, OSError):
            pass


def _load_qt_chinese(app):
    """让 Qt 自带的按钮（是/否、显示详细信息、文件对话框等）显示中文。"""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    tr = QTranslator(app)
    if tr.load(QLocale(QLocale.Chinese, QLocale.China), "qtbase", "_",
               QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
        app._qt_translator = tr        # 保持引用


def main():
    config.ensure_dirs()
    errors.setup_logging()
    _windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    _load_qt_chinese(app)
    errors.install_hooks()
    errors.make_reporter(lambda: QApplication.activeWindow())
    try:
        return _run(app)
    except Exception as e:  # noqa: BLE001  启动阶段出错也要让人看到
        log.exception("启动失败")
        import traceback
        errors.show_error_dialog(None, "启动失败", f"{type(e).__name__}: {e}", traceback.format_exc(),
                                 intro="程序没能启动。")
        return 1


def _run(app):
    # 这些导入放在这里：万一某个模块出错，也能被上面捕获并弹窗
    from .db import Database
    from .single_instance import SingleInstance, bring_to_front
    from .ui import fonts
    from .ui.main_window import MainWindow
    from .ui.style import ASSETS, DEFAULT_PT, apply_appearance, register_assets
    from .ui.workers import wait_all

    instance = SingleInstance()
    if not instance.acquire():
        log.info("已有程序在运行，已通知它显示到最前面")
        return 0                      # 已经开着一个了

    errors.enable_crash_dump()
    errors.start_hang_watchdog()
    crashed_last_time = errors.previous_run_crashed()
    errors.mark_running()
    errors.log_environment()

    app.setWindowIcon(QIcon(str(ASSETS / "icon.png")))
    app.setStyle("Fusion")
    register_assets()
    fonts.load_bundled()
    db = Database()
    try:
        pt = int(db.get_setting("font_pt") or DEFAULT_PT)
    except ValueError:
        pt = DEFAULT_PT
    apply_appearance(app, pt, db.get_setting("font_family") or fonts.DEFAULT_KEY)
    win = MainWindow(db)
    errors.make_reporter(lambda: win)
    instance.activate_requested.connect(lambda: bring_to_front(win))
    win.show()
    if crashed_last_time:
        QTimer.singleShot(600, lambda: errors.show_crash_notice(win))
    code = app.exec()
    wait_all()
    db.close()
    instance.release()
    errors.mark_clean_exit()
    return code
