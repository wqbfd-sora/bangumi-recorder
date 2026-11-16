"""只允许运行一个程序窗口。

同一个数据文件夹只能被一个进程打开（两个进程同时写一个数据库容易出问题）：
- 用数据文件夹里的锁文件（QLockFile）判断是否已经有程序在运行；程序异常退出留下的旧锁会被自动识别并清掉。
- 已经在运行时，新启动的进程通过本地连接（QLocalServer）通知旧窗口跳到最前面，然后自己退出。
"""
import getpass
import hashlib
import sys

from PySide6.QtCore import QLockFile, QObject, Qt, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import config

_MSG = b"activate"


def _server_name():
    key = f"{getpass.getuser()}|{config.DATA_DIR.resolve()}".lower()
    return "BangumiRecorder-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _allow_foreground():
    """Windows 默认不让后台进程抢前台；由刚被用户启动的新进程授权给旧窗口。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except (AttributeError, OSError):
            pass


def _notify_running():
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(1500):
        return False
    _allow_foreground()
    sock.write(_MSG)
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


class SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        config.ensure_dirs()
        self.lock = QLockFile(str(config.DATA_DIR / "app.lock"))
        self.lock.setStaleLockTime(0)        # 只按「持有锁的进程是否还活着」判断是否过期
        self.server = None

    def acquire(self):
        """拿到锁返回 True（本进程是唯一实例）；已有程序在运行则通知它并返回 False。"""
        if not self.lock.tryLock(200):
            if self.lock.error() == QLockFile.LockFailedError:
                if _notify_running():
                    return False
                # 锁文件说有程序在跑，但叫不应它（比如上次闪退后进程号被别的程序占用了）：
                # 当作残留的旧锁清掉，否则程序会一直打不开。
                self.lock.removeStaleLockFile()
                if not self.lock.tryLock(200):
                    return True               # 实在拿不到锁也不阻止启动
            else:
                return True                   # 锁文件读写出错（如权限问题）：不阻止启动
        name = _server_name()
        QLocalServer.removeServer(name)      # 清掉异常退出留下的旧 socket（Linux/macOS）
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._on_connection)
        self.server.listen(name)
        return True

    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        if bytes(sock.readAll()).startswith(_MSG):
            self.activate_requested.emit()

    def release(self):
        if self.server:
            self.server.close()
        self.lock.unlock()


def bring_to_front(win):
    if win.isMinimized():
        win.showNormal()
    win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
    win.show()
    win.raise_()
    win.activateWindow()
