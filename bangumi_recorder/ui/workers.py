"""后台任务：网络请求、下载、解码放到线程池里跑，界面不会卡住。

结果统一通过一个常驻主线程的「分发器」送回界面：
- 线程里只发信号、不持有任何会被主线程释放的 Qt 对象，避免「任务对象在线程还没跑完时就被释放」导致闪退；
- 回调要用的窗口如果已经关掉了（比如搜索框关闭后缩略图才下好），就安静地跳过。
"""
import itertools
import logging
import traceback

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from .. import config
from ..bangumi import BangumiError

log = logging.getLogger("bangumi_recorder.workers")
_ids = itertools.count(1)
_tasks = {}
_dispatcher = None


class _Box:
    """把结果包起来再跨线程传：Qt 会把 dict/tuple 转成 QVariant，int 作键的 dict 会丢内容。"""
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class Task:
    """run_async 的返回值。设置 task.cancelled = True 可以让支持取消的任务提前结束。"""
    __slots__ = ("id", "cancelled", "on_done", "on_fail", "on_progress", "name")

    def __init__(self, on_done, on_fail, on_progress, name):
        self.id = next(_ids)
        self.cancelled = False
        self.on_done, self.on_fail, self.on_progress = on_done, on_fail, on_progress
        self.name = name


class _Dispatcher(QObject):
    """常驻主线程。线程里 emit，主线程里调用回调。"""
    done = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, int, object)

    def __init__(self):
        super().__init__()
        self.done.connect(self._on_done)
        self.failed.connect(self._on_failed)
        self.progress.connect(self._on_progress)

    @staticmethod
    def _call(task, fn, *args):
        try:
            fn(*args)
        except RuntimeError as e:
            # 窗口已经关掉了：PySide 会报 "Internal C++ object already deleted"
            if "already deleted" in str(e):
                log.debug("任务 %s 的回调目标已关闭，跳过", task.name)
            else:
                raise

    @Slot(int, object)
    def _on_done(self, tid, box):
        task = _tasks.pop(tid, None)
        if task and task.on_done:
            self._call(task, task.on_done, box.value)

    @Slot(int, str)
    def _on_failed(self, tid, err):
        task = _tasks.pop(tid, None)
        if task and task.on_fail:
            self._call(task, task.on_fail, err)

    @Slot(int, int, int, object)
    def _on_progress(self, tid, i, n, box):
        task = _tasks.get(tid)
        if task and task.on_progress:
            self._call(task, task.on_progress, i, n, box.value)


def _get_dispatcher():
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _Dispatcher()      # 第一次调用一定在主线程（界面代码里）
    return _dispatcher


def run_async(fn, on_done=None, on_fail=None, on_progress=None, pool=None):
    """在后台运行 fn。带 on_progress 时 fn 的签名是 fn(report, is_cancelled)，
    report(i, n, payload=None) 用来汇报进度。返回 Task（可设 .cancelled）。"""
    d = _get_dispatcher()
    task = Task(on_done, on_fail, on_progress, getattr(fn, "__qualname__", "task"))
    _tasks[task.id] = task
    tid = task.id

    def emit(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:            # 程序正在退出，分发器已经销毁
            pass

    def runner():
        try:
            if on_progress is not None:
                res = fn(lambda i, n, payload=None: emit(d.progress, tid, i, n, _Box(payload)),
                         lambda: task.cancelled)
            else:
                res = fn()
        except BangumiError as e:
            log.warning("后台任务失败：%s", e)
            emit(d.failed, tid, str(e))
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("后台任务出错：\n%s", tb)
            emit(d.failed, tid, tb)
        else:
            emit(d.done, tid, _Box(res))

    (pool or QThreadPool.globalInstance()).start(runner)
    return task


def wait_all(timeout_ms=5000):
    """退出前调用：通知所有任务停下、清掉排队的任务，再等正在跑的结束。
    不通知的话，像「刷新 Bangumi 数据」这种长任务会让进程在窗口关掉后还挂一分钟。"""
    for task in list(_tasks.values()):
        task.cancelled = True
    pool = QThreadPool.globalInstance()
    pool.clear()
    pool.waitForDone(timeout_ms)
    _tasks.clear()


def cover_path(bgm_id, url):
    if not bgm_id or not url:
        return None
    ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = "jpg"
    return config.COVER_DIR / f"{bgm_id}.{ext}"


def fetch_cover(client, bgm_id, url):
    """返回本地封面路径（必要时先下载）。先写临时文件再改名，避免下载一半的坏文件。"""
    path = cover_path(bgm_id, url)
    if path is None:
        return None
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = client.download(url)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return str(path)
