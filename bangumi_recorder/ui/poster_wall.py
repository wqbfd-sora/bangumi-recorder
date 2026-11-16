"""封面海报墙：和表格共用同一个数据模型与选择，只是换一种画法。"""
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from .. import appconfig
from ..db import fmt_score, season_label
from .covers import placeholder
from .style import ACCENT, ACCENT_SOFT, MUTED, TEXT, score_color
from .table_model import RANK_ROLE

CARD_W, COVER_H = 150, 212
PAD = 8
TITLE_LINES = 2


def _row_of(index):
    model = index.model()
    src = model.mapToSource(index) if hasattr(model, "mapToSource") else index
    return src.model().rows[src.row()]


def _two_lines(text, fm, width):
    """中文没有空格，按字符折成最多两行，第二行放不下就加省略号。"""
    text = text or ""
    if fm.horizontalAdvance(text) <= width:
        return [text]
    n = 1
    while n < len(text) and fm.horizontalAdvance(text[:n + 1]) <= width:
        n += 1
    return [text[:n], fm.elidedText(text[n:], Qt.ElideRight, width)]


class PosterDelegate(QStyledItemDelegate):
    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.covers = covers
        self.title_font = QFont()
        self.title_font.setBold(True)
        self.title_font.setPointSizeF(9.5)
        self.sub_font = QFont()
        self.sub_font.setPointSizeF(8.5)
        self.badge_font = QFont()
        self.badge_font.setBold(True)
        self.badge_font.setPointSizeF(10.5)
        self.small_font = QFont()
        self.small_font.setBold(True)
        self.small_font.setPointSizeF(8)
        self._ph = {}

    def set_base_font(self, base):
        """卡片上的字号跟随全局字号。"""
        pt = base.pointSizeF() if base.pointSizeF() > 0 else 11
        for f, delta in ((self.title_font, -0.5), (self.sub_font, -1.5), (self.badge_font, 0.5),
                         (self.small_font, -2)):
            f.setFamilies(base.families() or [base.family()])
            f.setPointSizeF(max(7.0, pt + delta))

    def sizeHint(self, option, index):
        fm_t = QFontMetrics(self.title_font)
        fm_s = QFontMetrics(self.sub_font)
        text_h = fm_t.lineSpacing() * TITLE_LINES + fm_s.lineSpacing() + 10
        return QSize(CARD_W + PAD * 2, COVER_H + text_h + PAD * 2)

    def _placeholder(self, title, dpr):
        key = (title, dpr)
        if key not in self._ph:
            if len(self._ph) > 600:
                self._ph.clear()
            self._ph[key] = placeholder(title, CARD_W, COVER_H, dpr)
        return self._ph[key]

    def paint(self, p, option, index):
        r = _row_of(index)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cell = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)

        # 卡片底
        if selected or hover:
            bg = QPainterPath()
            bg.addRoundedRect(cell, 12, 12)
            p.fillPath(bg, QColor(ACCENT_SOFT) if selected else QColor("#ECEEF1"))
            if selected:
                p.setPen(QPen(QColor(ACCENT), 1.5))
                p.drawPath(bg)

        # 封面（圆角裁切，居中铺满）
        cover = QRectF(option.rect.x() + PAD, option.rect.y() + PAD, CARD_W, COVER_H)
        clip = QPainterPath()
        clip.addRoundedRect(cover, 10, 10)
        p.setClipPath(clip)
        pm = self.covers.get(r)
        if pm is None or pm.isNull():
            dpr = p.device().devicePixelRatioF() if p.device() else 1.0
            p.drawPixmap(cover.topLeft(), self._placeholder(r.get("title"), dpr))
        else:
            src_w, src_h = pm.width(), pm.height()
            scale = max(CARD_W / src_w, COVER_H / src_h)
            sw, sh = CARD_W / scale, COVER_H / scale
            src = QRectF((src_w - sw) / 2, (src_h - sh) / 2, sw, sh)
            p.drawPixmap(cover, pm, src)
        p.setClipping(False)

        # 评分角标（右上）
        score = r.get("score")
        text = fmt_score(score) if score is not None else (r.get("score_note") or "")
        if text:
            p.setFont(self.badge_font)
            fm = QFontMetrics(self.badge_font)
            bw = max(fm.horizontalAdvance(text) + 14, 34)
            bh = fm.height() + 4
            badge = QRectF(cover.right() - bw - 6, cover.top() + 6, bw, bh)
            path = QPainterPath()
            path.addRoundedRect(badge, bh / 2, bh / 2)
            color = score_color(score) if score is not None else QColor("#9CA3AF")
            p.fillPath(path, color)
            p.setPen(Qt.white)
            p.drawText(badge, Qt.AlignCenter, text)

        # 名次（左上，总榜里才有）
        rank = index.data(RANK_ROLE)
        if rank:
            p.setFont(self.small_font)
            fm = QFontMetrics(self.small_font)
            t = f"#{rank}"
            bw = fm.horizontalAdvance(t) + 12
            bh = fm.height() + 4
            badge = QRectF(cover.left() + 6, cover.top() + 6, bw, bh)
            path = QPainterPath()
            path.addRoundedRect(badge, bh / 2, bh / 2)
            gold = {1: "#D4A017", 2: "#9AA4B1", 3: "#B87333"}.get(rank)
            p.fillPath(path, QColor(gold) if gold else QColor(0, 0, 0, 150))
            p.setPen(Qt.white)
            p.drawText(badge, Qt.AlignCenter, t)

        # 状态（左下）
        info = appconfig.current().status_info(r.get("status"))
        status = info["name"] if info and info.get("badge") else None
        if status:
            p.setFont(self.small_font)
            fm = QFontMetrics(self.small_font)
            bw = fm.horizontalAdvance(status) + 12
            bh = fm.height() + 4
            badge = QRectF(cover.left() + 6, cover.bottom() - bh - 6, bw, bh)
            path = QPainterPath()
            path.addRoundedRect(badge, 5, 5)
            p.fillPath(path, QColor(info["color"]))
            p.setPen(Qt.white)
            p.drawText(badge, Qt.AlignCenter, status)

        # Bangumi 小圆点（右下）：已关联
        if r.get("bgm_id"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawEllipse(QPointF(cover.right() - 11, cover.bottom() - 11), 5, 5)
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(QPointF(cover.right() - 11, cover.bottom() - 11), 3, 3)

        # 标题（两行）+ 副标题
        y = cover.bottom() + 6
        p.setFont(self.title_font)
        p.setPen(QColor(TEXT))
        fm_t = QFontMetrics(self.title_font)
        for line in _two_lines(r.get("title"), fm_t, CARD_W):
            p.drawText(QRect(int(cover.left()), int(y), CARD_W, fm_t.lineSpacing()),
                       Qt.AlignLeft | Qt.AlignVCenter, line)
            y += fm_t.lineSpacing()
        y = cover.bottom() + 6 + fm_t.lineSpacing() * TITLE_LINES + 2
        p.setFont(self.sub_font)
        p.setPen(QColor(MUTED))
        fm_s = QFontMetrics(self.sub_font)
        sub = " · ".join(x for x in (
            season_label(r.get("season")) if r.get("season") else None, r.get("format")) if x)
        p.drawText(QRect(int(cover.left()), int(y), CARD_W, fm_s.lineSpacing()),
                   Qt.AlignLeft | Qt.AlignVCenter, fm_s.elidedText(sub, Qt.ElideRight, CARD_W))
        p.restore()


class PosterWall(QListView):
    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.setObjectName("wall")
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setDragEnabled(False)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.delegate = PosterDelegate(covers, self)
        self.setItemDelegate(self.delegate)
        covers.updated.connect(lambda _bid: self.viewport().update())

    def showEvent(self, e):
        self.delegate.set_base_font(self.font())
        super().showEvent(e)
        self._fit_grid()

    def refresh_fonts(self, font):
        self.delegate.set_base_font(font)
        self.setGridSize(QSize())
        self._fit_grid()
        self.doItemsLayout()
        self.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_grid()

    def _fit_grid(self):
        """把多余的宽度平均分给每一列，让卡片铺满整行。"""
        item = self.delegate.sizeHint(None, None)
        # 宽度按「一直留着滚动条的位置」来算：否则在三列/四列的临界宽度上，
        # 列数变 → 行数变 → 滚动条出现/消失 → 可用宽度变 → 列数又变，来回闪个不停。
        sb = self.verticalScrollBar()
        sb_w = max(sb.sizeHint().width(), 14)
        full = self.viewport().width() + (sb.width() if sb.isVisible() else 0)
        avail = full - sb_w - 2 * self.spacing() - 4
        cols = max(1, avail // (item.width() + self.spacing()))
        gw = max(item.width(), avail // cols)
        size = QSize(gw, item.height() + self.spacing())
        if self.gridSize() != size:
            self.setGridSize(size)
