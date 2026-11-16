"""作品列表：去掉 Excel 式的网格，整行悬停/选中，状态、体裁、评级画成小标签。
状态格、体裁格按住就弹出选项，拖到想要的那一项松开即可改；也可以点一下再点选。"""
from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QMenu, QStyle, QStyledItemDelegate,
                               QTableView)

from .. import appconfig
from ..db import fmt_score, score_tier
from .style import ACCENT, ACCENT_SOFT, MUTED, TEXT, score_color
from .table_model import ID_ROLE, RANK_ROLE, ROW_ROLE

MUTED_COLS = {"season", "studio", "director", "series_comp", "short_name", "episodes", "air_date",
              "weekday", "tags", "comment", "bgm_score"}
PILL_COLS = {"status", "format", "tier"}
HOVER_BG = QColor("#F7F8FA")
LINE = QColor("#F0F1F3")
PAD_X = 10
FORMAT_COLOR = "#64748B"
PICK_FIELDS = ("status", "format")          # 能按住拖选的列


def _dot_icon(color, size=12):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    p.end()
    return QIcon(pm)


def _pill(p, rect, text, color, font, solid=False):
    """圆角小标签：浅色底 + 同色文字（solid=True 时是实心底白字）。"""
    fm = QFontMetrics(font)
    w = min(fm.horizontalAdvance(text) + 16, rect.width() - 4)
    h = min(fm.height() + 4, rect.height() - 6)
    box = QRectF(rect.x() + (rect.width() - w) / 2, rect.y() + (rect.height() - h) / 2, w, h)
    path = QPainterPath()
    path.addRoundedRect(box, h / 2, h / 2)
    c = QColor(color)
    if solid:
        p.fillPath(path, c)
        p.setPen(Qt.white)
    else:
        bg = QColor(c)
        bg.setAlpha(34)
        p.fillPath(path, bg)
        p.setPen(c.darker(115))
    p.setFont(font)
    p.drawText(box, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(w - 8)))


class RowDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def paint(self, p, option, index):
        col = self.view.col_key(index.column())
        r = index.data(ROW_ROLE) or {}
        rect = option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)

        # 整行底色 + 行与行之间一条很淡的线
        selected = bool(option.state & QStyle.State_Selected)
        hover = index.row() == self.view.hover_row
        if selected:
            p.fillRect(rect, QColor(ACCENT_SOFT))
        elif hover:
            p.fillRect(rect, HOVER_BG)
        p.setPen(LINE)
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected and index.column() == self.view.first_visible_column():
            p.fillRect(QRectF(rect.x(), rect.y() + 6, 3, rect.height() - 12), QColor(ACCENT))

        font = QFont(option.font)
        f_role = index.data(Qt.FontRole)
        if f_role is not None:
            font = QFont(f_role)
            font.setPointSizeF(option.font.pointSizeF())
            font.setFamilies(option.font.families())
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.5))

        if col == "status":
            st = appconfig.current().status_info(r.get("status"))
            if st:
                _pill(p, rect, st["name"], st["color"], small)
            else:
                self._muted_dash(p, rect, small)
        elif col == "format":
            if r.get("format"):
                _pill(p, rect, r["format"], FORMAT_COLOR, small)
            else:
                self._muted_dash(p, rect, small)
        elif col == "tier":
            sc = r.get("score")
            name = score_tier(sc) if sc is not None else ""
            if name:
                _pill(p, rect, name, score_color(sc).name(), small)
        elif col == "score":
            sc = r.get("score")
            if sc is not None:
                f = QFont(font)
                f.setBold(True)
                f.setPointSizeF(option.font.pointSizeF() + 0.5)
                p.setFont(f)
                p.setPen(score_color(sc))
                p.drawText(rect, Qt.AlignCenter, fmt_score(sc))
            elif r.get("score_note"):
                p.setFont(small)
                p.setPen(QColor(MUTED))
                p.drawText(rect, Qt.AlignCenter, r["score_note"])
        elif col == "bgm":
            if r.get("bgm_id"):
                c = rect.center()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(ACCENT))
                p.drawEllipse(QRectF(c.x() - 3.5, c.y() - 3.5, 7, 7))
        elif col == "rank":
            rank = index.data(RANK_ROLE)
            if rank:
                gold = {1: "#D4A017", 2: "#9AA4B1", 3: "#B87333"}.get(rank)
                if gold:
                    _pill(p, rect, str(rank), gold, small, solid=True)
                else:
                    p.setFont(small)
                    p.setPen(QColor(MUTED))
                    p.drawText(rect, Qt.AlignCenter, str(rank))
        else:
            text = index.data(Qt.DisplayRole) or ""
            align = index.data(Qt.TextAlignmentRole) or int(Qt.AlignLeft | Qt.AlignVCenter)
            p.setFont(font)
            p.setPen(QColor(MUTED) if col in MUTED_COLS else QColor(TEXT))
            box = rect.adjusted(PAD_X, 0, -PAD_X // 2, 0)
            text = text.replace("\n", " ")
            text = QFontMetrics(font).elidedText(text, Qt.ElideRight, box.width())
            p.drawText(box, int(align), text)
        p.restore()

    @staticmethod
    def _muted_dash(p, rect, font):
        p.setFont(font)
        p.setPen(QColor("#C9CDD3"))
        p.drawText(rect, Qt.AlignCenter, "—")

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width() + PAD_X, s.height())


class DragMenu(QMenu):
    """按住鼠标弹出、拖到某一项松开就选中的菜单（像 macOS 的弹出菜单）。
    只是点一下（按下马上松开）则菜单留着，再点一项选中。"""

    def mouseReleaseEvent(self, e):
        a = self.actionAt(e.position().toPoint())
        if a is not None and a.isEnabled() and not a.isSeparator() and self.rect().contains(
                e.position().toPoint()):
            self.hide()
            a.trigger()
            return
        super().mouseReleaseEvent(e)


class AnimeTable(QTableView):
    field_chosen = Signal(int, str, object)  # (作品 id, 字段 status/format, 新值；None = 清空)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1
        self.col_keys = []
        self.setObjectName("animeTable")
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setStretchLastSection(True)
        self.setItemDelegate(RowDelegate(self))
        self.can_pick = lambda row_id: True     # 主窗口会换成真正的判断（右边有没保存的修改时不弹）

    def set_columns(self, keys):
        self.col_keys = list(keys)

    def col_key(self, column):
        return self.col_keys[column] if 0 <= column < len(self.col_keys) else None

    def first_visible_column(self):
        return self.horizontalHeader().logicalIndex(0)

    # ---------- 悬停整行高亮 ----------
    def mouseMoveEvent(self, e):
        row = self.rowAt(e.position().toPoint().y())
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    # ---------- 状态 / 体裁格：按住弹出选项 ----------
    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        field = self.col_key(idx.column()) if idx.isValid() else None
        if e.button() != Qt.LeftButton or field not in PICK_FIELDS:
            super().mousePressEvent(e)
            return
        row_id = idx.data(ID_ROLE)
        super().mousePressEvent(e)                # 先正常选中这一行
        if self.can_pick(row_id):
            self.popup_menu(idx, field)

    def popup_status_menu(self, idx):
        return self.popup_menu(idx, "status")

    def popup_menu(self, idx, field):
        row = idx.data(ROW_ROLE) or {}
        row_id = idx.data(ID_ROLE)
        current = row.get(field)
        cfg = appconfig.current()
        menu = DragMenu(self)
        menu.setObjectName("pickMenu")
        names = cfg.status_names() if field == "status" else cfg.formats()
        if current and current not in names:
            names = names + [current]
        for name in names:
            color = cfg.status_info(name)["color"] if field == "status" else FORMAT_COLOR
            a = QAction(_dot_icon(color), name, menu)
            a.setCheckable(True)
            a.setChecked(name == current)
            a.triggered.connect(lambda _c=False, v=name: self.field_chosen.emit(row_id, field, v))
            menu.addAction(a)
        menu.addSeparator()
        clear = QAction("清空", menu)
        clear.setEnabled(bool(current))
        clear.triggered.connect(lambda _c=False: self.field_chosen.emit(row_id, field, None))
        menu.addAction(clear)
        rect = self.visualRect(idx)
        menu.popup(self.viewport().mapToGlobal(QPoint(rect.left(), rect.bottom() + 2)))
        self._menu = menu                   # 测试里要用
        return menu
