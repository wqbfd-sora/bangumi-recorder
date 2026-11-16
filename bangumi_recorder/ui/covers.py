"""封面缓存与占位海报。"""
import hashlib

from PySide6.QtCore import QObject, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImageReader, QLinearGradient, QPainter, QPixmap

from .workers import fetch_cover, run_async

# 占位海报的配色（按作品名散列挑一组）
PALETTES = [
    ("#F59AA5", "#C9505E"), ("#8EC5FC", "#4F7CC9"), ("#A8E6CF", "#3D9970"),
    ("#FFD3A5", "#E07A3F"), ("#C3B1E1", "#7E57C2"), ("#9FD8DF", "#2C8C99"),
    ("#F6C1D8", "#B0477B"), ("#B8C6DB", "#56657F"),
]


def placeholder(title, w, h, dpr=1.0):
    """没有封面时生成一张渐变色海报，中间是作品名的第一个字。"""
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    key = int(hashlib.md5((title or "?").encode("utf-8")).hexdigest(), 16)
    c1, c2 = PALETTES[key % len(PALETTES)]
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(c1))
    g.setColorAt(1, QColor(c2))
    p.fillRect(QRectF(0, 0, w, h), g)
    ch = (title or "?").strip()[:1] or "?"
    f = QFont()
    f.setPixelSize(int(h * 0.36))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 215))
    p.drawText(QRectF(0, 0, w, h * 0.92), Qt.AlignCenter, ch)
    p.end()
    return pm


def _load_image(client, bid, url, max_h):
    """后台线程里执行：必要时下载，然后解码并缩小（QImage 可以在线程里用，QPixmap 不行）。"""
    path = fetch_cover(client, bid, url)
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.height() > max_h:
        reader.setScaledSize(size.scaled(10 ** 6, max_h, Qt.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        raise ValueError(f"无法读取封面 {path}")
    return img


class CoverCache(QObject):
    """按 Bangumi ID 缓存封面。下载和解码都在后台（最多 4 个同时进行），完成后发 updated 信号。"""
    updated = Signal(int)
    MAX_H = 480

    def __init__(self, client_getter, parent=None):
        super().__init__(parent)
        self.client_getter = client_getter
        self.mem = {}
        self.pending = set()
        self.failed = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)

    def get(self, row):
        bid, url = row.get("bgm_id"), row.get("cover_url")
        if not bid or not url:
            return None
        if bid in self.mem:
            return self.mem[bid]
        if bid not in self.pending and bid not in self.failed:
            self.pending.add(bid)
            client = self.client_getter()
            run_async(lambda: _load_image(client, bid, url, self.MAX_H),
                      lambda img, b=bid: self._loaded(b, img),
                      lambda _e, b=bid: self._failed(b),
                      pool=self.pool)
        return None

    def _loaded(self, bid, img):
        self.pending.discard(bid)
        self.mem[bid] = QPixmap.fromImage(img)
        self.updated.emit(bid)

    def _failed(self, bid):
        self.pending.discard(bid)
        self.failed.add(bid)

    def forget(self, bid):
        self.mem.pop(bid, None)
        self.failed.discard(bid)
