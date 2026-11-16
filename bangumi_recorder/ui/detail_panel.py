"""右侧详情/编辑面板。"""
import json
import math
import re

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from .. import appconfig, config
from ..bangumi import SUBJECT_URL
from ..db import extra_of, fmt_score, score_tier, season_label
from .style import score_color
from .covers import placeholder
from .workers import cover_path, fetch_cover, run_async

COVER_W, COVER_H = 150, 212
_FULLWIDTH = str.maketrans("０１２３４５６７８９．，－", "0123456789..-")


def parse_score(raw):
    """评分格：数字 → (分数, None)；空 → (None, None)；其他文字 → (None, 原文，存为评分备注)。
    8,5 / ８．５ 这类写法也按 8.5 处理。"""
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw).strip()
    norm = text.translate(_FULLWIDTH).replace("，", ".")
    if re.fullmatch(r"-?\d+,\d+", norm):
        norm = norm.replace(",", ".")
    try:
        val = float(norm)
    except ValueError:
        return None, text
    if math.isnan(val) or math.isinf(val):
        return None, text
    return val, None


def _rounded(pm, radius=10):
    """裁成圆角、居中铺满封面框。"""
    dpr = pm.devicePixelRatio()
    out = QPixmap(int(COVER_W * dpr), int(COVER_H * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, COVER_W, COVER_H), radius, radius)
    p.setClipPath(path)
    w, h = pm.width() / dpr, pm.height() / dpr
    p.drawPixmap(QRectF((COVER_W - w) / 2, (COVER_H - h) / 2, w, h), pm, QRectF(pm.rect()))
    p.end()
    return out


class NoWheelComboBox(QComboBox):
    """滚动侧边栏时，鼠标划过下拉框不会改掉里面的值（以前滚一下就把星期、体裁、状态改了）。
    要改请点开下拉框，或者点进去以后用键盘上下键。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.view().isVisible():
            super().wheelEvent(e)
        else:
            e.ignore()                       # 交给外面的滚动区域去滚动


class DetailPanel(QScrollArea):
    save_requested = Signal(int, dict)      # (id, 字段)
    save_close_requested = Signal(int, dict)  # 回车：保存并关闭侧边栏
    close_requested = Signal()
    delete_requested = Signal(int)
    link_requested = Signal(int)            # 关联/重新关联 Bangumi
    refresh_requested = Signal(int)         # 从 Bangumi 补全

    def __init__(self, client_getter, parent=None, db=None):
        super().__init__(parent)
        self.db = db
        self._pending_breakdown = None     # 计算器算出、还没保存的分项
        self.setObjectName("detail")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(300)
        self.client_getter = client_getter
        self.row = None
        self._loading = False
        self._dirty = False

        inner = QWidget()
        inner.setObjectName("detailInner")
        self.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        # ---- 头部：封面 + 标题 + 分数 ----
        head = QHBoxLayout()
        self.cover = QLabel("无封面")
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(COVER_W, COVER_H)
        self.cover.setAlignment(Qt.AlignCenter)
        head.addWidget(self.cover, 0, Qt.AlignTop)
        hv = QVBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.romaji = QLabel()
        self.romaji.setObjectName("romaji")
        self.romaji.setWordWrap(True)
        self.romaji.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        self.big_score = QLabel()
        self.big_score.setObjectName("bigScore")
        self.tier = QLabel()
        self.tier.setObjectName("tier")
        self.tier.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.bgm_info = QLabel()
        self.bgm_info.setObjectName("subtitle")
        self.bgm_info.setWordWrap(True)
        self.bgm_info.setOpenExternalLinks(True)
        for w in (self.title, self.romaji, self.subtitle, self.big_score, self.tier, self.bgm_info):
            hv.addWidget(w)
        hv.setAlignment(self.tier, Qt.AlignLeft)
        hv.addStretch()
        head.addLayout(hv, 1)
        self.btn_close = QToolButton()
        self.btn_close.setObjectName("panelClose")
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("关闭侧边栏（Esc）")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.close_requested.emit)
        head.addWidget(self.btn_close, 0, Qt.AlignTop)
        v.addLayout(head)

        # ---- Bangumi 操作 ----
        bl = QHBoxLayout()
        self.btn_link = QPushButton("关联 Bangumi…")
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setToolTip("重新获取这部作品的 Bangumi 信息：播出季度和开播时间以 Bangumi 为准，"
                                    "其他字段只填空白的。\n不会改动评分、状态、个人评价和作品名")
        self.btn_open = QPushButton("打开网页")
        for b in (self.btn_link, self.btn_refresh, self.btn_open):
            bl.addWidget(b)
        v.addLayout(bl)
        self.btn_link.clicked.connect(lambda: self.row and self.link_requested.emit(self.row["id"]))
        self.btn_refresh.clicked.connect(lambda: self.row and self.refresh_requested.emit(self.row["id"]))
        self.btn_open.clicked.connect(self._open_web)

        # ---- 表单 ----
        sec = QLabel("我的记录")
        sec.setObjectName("section")
        v.addWidget(sec)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.w = {}

        def line(key, label, placeholder=""):
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.textEdited.connect(self._mark_dirty)
            e.returnPressed.connect(self.save_and_close)
            self.w[key] = e
            form.addRow(label, e)

        def combo(key, label, items):
            c = NoWheelComboBox()
            c.setEditable(True)
            c.setInsertPolicy(QComboBox.NoInsert)
            c.addItems([""] + items)
            c.currentTextChanged.connect(self._mark_dirty)
            c.lineEdit().returnPressed.connect(self.save_and_close)
            self.w[key] = c
            form.addRow(label, c)

        # 终评 + 评分计算器
        score_edit = QLineEdit()
        score_edit.setPlaceholderText("如 8.5；可留空")
        score_edit.setObjectName("scoreEdit")
        score_edit.textEdited.connect(self._mark_dirty)
        score_edit.returnPressed.connect(self.save_and_close)
        self.w["score"] = score_edit
        self.btn_calc = QPushButton("计算器")
        self.btn_calc.setToolTip("按 剧情 : 制作 : 节奏（默认 5 : 3 : 2）算出终评，向上取整到一位小数")
        self.btn_calc.clicked.connect(self._open_calc)
        self.breakdown = QLabel()
        self.breakdown.setObjectName("subtitle")
        self.breakdown.setWordWrap(True)
        score_box = QWidget()
        sv = QVBoxLayout(score_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)
        sh = QHBoxLayout()
        sh.setSpacing(6)
        sh.addWidget(score_edit, 1)
        sh.addWidget(self.btn_calc)
        sv.addLayout(sh)
        sv.addWidget(self.breakdown)
        form.addRow("终评", score_box)
        combo("status", "状态", appconfig.current().status_names())
        line("title", "名称")
        line("romaji", "罗马音", "日文原名的罗马音，如 Kusuriya no Hitorigoto")
        line("short_name", "简称")
        line("season", "季度", "YYMM，如 2507；老番可写 16年")
        self.backlog = QCheckBox("放在「补番」里")
        self.backlog.toggled.connect(self._mark_dirty)
        form.addRow("", self.backlog)
        combo("format", "体裁", appconfig.current().formats())
        line("tags", "题材", "用 / 分隔")
        combo("weekday", "星期", config.WEEKDAYS)
        line("studio", "制作公司")
        line("director", "监督")
        line("series_comp", "系列构成")
        line("episodes", "正片集数")
        line("air_date", "开播时间", "yyyy-mm-dd")
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("个人评价")
        self.comment.setFixedHeight(80)
        self.comment.textChanged.connect(self._mark_dirty)
        self.comment.setToolTip("回车换行；Ctrl+回车 保存并关闭")
        form.addRow("个人评价", self.comment)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, self.save_and_close, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Esc"), self, self.close_requested.emit, context=Qt.WidgetWithChildrenShortcut)
        v.addLayout(form)

        self.note = QLabel()
        self.note.setObjectName("subtitle")
        self.note.setWordWrap(True)
        v.addWidget(self.note)

        # ---- 保存 / 删除 ----
        al = QHBoxLayout()
        self.btn_save = QPushButton("保存  (Ctrl+S)")
        self.btn_save.setObjectName("primary")
        self.btn_revert = QPushButton("撤销修改")
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setObjectName("danger")
        al.addWidget(self.btn_save, 1)
        al.addWidget(self.btn_revert)
        al.addWidget(self.btn_delete)
        v.addLayout(al)
        self.btn_save.clicked.connect(self.save)
        self.btn_revert.clicked.connect(lambda: self.show_row(self.row))
        self.btn_delete.clicked.connect(lambda: self.row and self.delete_requested.emit(self.row["id"]))

        # ---- 简介 ----
        self.summary_title = QLabel("简介")
        self.summary_title.setObjectName("section")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(self.summary_title)
        v.addWidget(self.summary)
        v.addStretch()

        self.show_row(None)

    # ---------- 显示 ----------
    def show_row(self, row):
        self._loading = True
        self.row = row
        enabled = row is not None
        self._pending_breakdown = None
        for wdg in list(self.w.values()) + [self.comment, self.backlog, self.btn_save, self.btn_revert,
                                             self.btn_delete, self.btn_link, self.btn_calc]:
            wdg.setEnabled(enabled)
        if not row:
            self.title.setText("选择一部作品")
            for lab in (self.romaji, self.subtitle, self.big_score, self.tier, self.bgm_info, self.note,
                        self.summary):
                lab.setText("")
            self.tier.setVisible(False)
            self._show_breakdown(None)
            for wdg in self.w.values():
                wdg.setCurrentText("") if isinstance(wdg, QComboBox) else wdg.setText("")
            self.comment.setPlainText("")
            self.backlog.setChecked(False)
            self._set_cover(None)
            self.btn_refresh.setEnabled(False)
            self.btn_open.setEnabled(False)
            self.summary_title.hide()
            self._loading = False
            self._dirty = False
            return

        self.title.setText(row["title"])
        self.romaji.setText(row.get("romaji") or "")
        self.romaji.setVisible(bool(row.get("romaji")))
        sub = [season_label(row.get("season")) if row.get("season") else None,
               "补番" if row.get("is_backlog") else None, row.get("format"), row.get("studio")]
        self.subtitle.setText(" · ".join(x for x in sub if x))
        sc = row.get("score")
        if sc is not None:
            self.big_score.setText(fmt_score(sc))
            self._paint_score(sc)
            self.tier.setVisible(bool(score_tier(sc)))
        else:
            self.big_score.setText(row.get("score_note") or "未评分")
            self.big_score.setStyleSheet("color: #9CA3AF; font-size: 14pt;")
            self.tier.setText("")
            self.tier.setVisible(False)
        if row.get("bgm_id"):
            parts = [f'<a style="color:#C9505E; text-decoration:none; font-weight:bold;" '
                     f'href="{SUBJECT_URL.format(row["bgm_id"])}">Bangumi #{row["bgm_id"]}</a>']
            if row.get("bgm_score"):
                parts.append(f"评分 {row['bgm_score']:.1f}")
            if row.get("bgm_rank"):
                parts.append(f"排名 #{row['bgm_rank']}")
            txt = " · ".join(parts)
            if row.get("bgm_name"):
                txt += f"<br>日文原名：{row['bgm_name']}"
            self.bgm_info.setText(txt)
            self.btn_link.setText("重新关联…")
        else:
            self.bgm_info.setText("还没有关联 Bangumi 条目")
            self.btn_link.setText("关联 Bangumi…")
        self.btn_refresh.setEnabled(bool(row.get("bgm_id")))
        self.btn_open.setEnabled(bool(row.get("bgm_id")))

        for key, wdg in self.w.items():
            if key == "score":
                val = fmt_score(sc) if sc is not None else (row.get("score_note") or "")
            else:
                val = row.get(key)
                val = "" if val is None else str(val)
            if isinstance(wdg, QComboBox):
                wdg.setCurrentText(val)
            else:
                wdg.setText(val)
        self.comment.setPlainText(row.get("comment") or "")
        self.backlog.setChecked(bool(row.get("is_backlog")))

        ex = extra_of(row)
        notes = []
        if "开播时间原值" in ex:
            notes.append(f"开播时间在 Excel 里写的是「{ex['开播时间原值']}」，日期是按「月.日」推断的，可能不准。")
        if ex.get("罗马音来源") == "自动转写":
            notes.append("罗马音是按日文读音自动转写的（Bangumi 和 AniList 上都没查到），汉字读音可能不准，建议核对。")
        self._show_breakdown(ex.get("评分明细"))
        others = {k: v for k, v in ex.items()
                  if k not in ("开播时间原值", "罗马音来源", "评分明细", "年份", "季度")}
        if others:
            notes.append("其他导入信息：" + "；".join(f"{k}={v}" for k, v in others.items()))
        self.note.setText("\n".join(notes))

        self.summary.setText(row.get("summary") or "")
        self.summary_title.setVisible(bool(row.get("summary")))
        self._set_cover(row)
        self._loading = False
        self._dirty = False

    def _set_cover(self, row):
        self.cover.setPixmap(QPixmap())
        self.cover.setText("")
        if not row:
            return
        if not row.get("cover_url") or not row.get("bgm_id"):
            self.cover.setPixmap(_rounded(placeholder(row.get("title"), COVER_W, COVER_H,
                                                      self.devicePixelRatioF())))
            return
        path = cover_path(row["bgm_id"], row["cover_url"])
        if path and path.exists():
            self._apply_cover(row["id"], str(path))
            return
        self.cover.setText("加载中…")
        rid, bid, url = row["id"], row["bgm_id"], row["cover_url"]
        client = self.client_getter()          # 在主线程里取：第一次取会读数据库里的设置
        run_async(lambda: fetch_cover(client, bid, url),
                  lambda p: self._apply_cover(rid, p),
                  lambda _e: self.row and self.row["id"] == rid and self.cover.setText("封面加载失败"))

    def _apply_cover(self, rid, path):
        if not self.row or self.row["id"] != rid or not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.cover.setPixmap(_rounded(placeholder(self.row.get("title"), COVER_W, COVER_H,
                                                      self.devicePixelRatioF())))
            return
        dpr = self.devicePixelRatioF()
        pm = pm.scaled(int(COVER_W * dpr), int(COVER_H * dpr), Qt.KeepAspectRatioByExpanding,
                       Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.cover.setPixmap(_rounded(pm))

    def _open_web(self):
        if self.row and self.row.get("bgm_id"):
            QDesktopServices.openUrl(QUrl(SUBJECT_URL.format(self.row["bgm_id"])))

    # ---------- 编辑 ----------
    def _mark_dirty(self, *_):
        if not self._loading:
            self._dirty = True

    def is_dirty(self):
        return self.row is not None and self._dirty

    def collect(self):
        """读出表单内容。评分格：数字 → 评分；空 → 无；其他文字 → 评分备注。"""
        data = {}
        for key, wdg in self.w.items():
            txt = (wdg.currentText() if isinstance(wdg, QComboBox) else wdg.text()).strip()
            data[key] = txt or None
        raw = data.pop("score")
        data["score"], data["score_note"] = parse_score(raw)
        data["comment"] = self.comment.toPlainText().strip() or None
        data["is_backlog"] = int(self.backlog.isChecked())
        if self.row:
            ex = extra_of(self.row)
            changed = False
            if data.get("romaji") != self.row.get("romaji") and "罗马音来源" in ex:
                ex.pop("罗马音来源")             # 手动改过罗马音 → 不再标记为自动转写
                changed = True
            if self._pending_breakdown is not None:
                ex["评分明细"] = self._pending_breakdown
                changed = True
            if changed:
                data["extra"] = json.dumps(ex, ensure_ascii=False) if ex else None
        return data

    # ---------- 评分计算器 ----------
    def _show_breakdown(self, scores):
        if not scores:
            self.breakdown.setText("")
            self.breakdown.setVisible(False)
            return
        self.breakdown.setText(" · ".join(f"{k} {fmt_score(v)}" for k, v in scores.items()))
        self.breakdown.setVisible(True)

    def _open_calc(self):
        if not self.row or self.db is None:
            return
        from .score_calc import ScoreCalculator
        prev = self._pending_breakdown or extra_of(self.row).get("评分明细")
        dlg = ScoreCalculator(self.db, self.w["title"].text() or self.row["title"], prev, self)
        if dlg.exec() and dlg.result is not None:
            self.w["score"].setText(fmt_score(float(dlg.result)))
            self._pending_breakdown = dlg.scores
            self._show_breakdown(dlg.scores)
            self._mark_dirty()

    def save(self):
        if self.row:
            self.save_requested.emit(self.row["id"], self.collect())

    def save_and_close(self):
        """回车：保存，保存成功后关闭侧边栏（没有改动就直接关闭）。"""
        if not self.row:
            return
        self.save_close_requested.emit(self.row["id"], self.collect())

    def reload_options(self):
        """配置文件里的状态 / 体裁改了：更新下拉选项，保留当前填的值。"""
        cfg = appconfig.current()
        for key, items in (("status", cfg.status_names()), ("format", cfg.formats())):
            c = self.w[key]
            was_loading, self._loading = self._loading, True
            text = c.currentText()
            c.blockSignals(True)
            c.clear()
            c.addItems([""] + items)
            c.setCurrentText(text)
            c.blockSignals(False)
            self._loading = was_loading
        if self.row and self.row.get("score") is not None:
            self._paint_score(self.row["score"])

    def _paint_score(self, sc):
        """大号分数和评级标签用这一档评分标准的颜色。"""
        c = score_color(sc)
        self.big_score.setStyleSheet(f"color: {c.name()};")
        self.tier.setText(score_tier(sc))
        self.tier.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},36); color: {c.name()};")
