"""网络连接池。

不要用 threading.local 给每个线程一个 requests.Session：线程池里的线程结束任务时，
它的 Session 会在线程清理阶段被回收、去关闭网络连接，在 Windows 上会和主线程互相等待，
导致界面「未响应」甚至闪退（1.0.4–1.0.5 的问题）。

这里改成一个「借用-归还」的 Session 池：同一时间一个 Session 只给一个线程用，
用完放回池里；所有 Session 都跟着客户端对象活在主线程里，不会在线程清理时被回收。
"""
import queue
from contextlib import contextmanager

import requests
from requests.adapters import HTTPAdapter


class SessionPool:
    def __init__(self, headers=None, max_idle=8):
        self.headers = dict(headers or {})
        self._idle = queue.LifoQueue(maxsize=max_idle)
        self._all = []                    # 保持引用，避免在别的线程里被回收

    def _new(self):
        s = requests.Session()
        s.headers.update(self.headers)
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        self._all.append(s)
        return s

    @contextmanager
    def session(self):
        try:
            s = self._idle.get_nowait()
        except queue.Empty:
            s = self._new()
        try:
            yield s
        finally:
            try:
                self._idle.put_nowait(s)
            except queue.Full:
                pass                      # 池满了：仍由 self._all 持有，主线程退出时统一回收

    def set_header(self, key, value):
        self.headers[key] = value
        for s in self._all:
            s.headers[key] = value
