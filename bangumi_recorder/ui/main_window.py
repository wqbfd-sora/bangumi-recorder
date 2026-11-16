"""主窗口：左边导航（总榜 / 全部 / 补番 / 各季度），中间表格，右边详情。"""
import logging
import os
import re
from datetime import date
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QProgressDialog, QSplitter, QStackedWidget,
                               QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__, appconfig, config, errors
from ..bangumi import BangumiClient, full_details, merge_fields, norm_key, with_romaji_source
from ..db import extra_of
from ..romaji import (SOURCE_ANILIST, SOURCE_BANGUMI, SOURCE_KAKASI, AniListClient, from_aliases,
                      lookup_many, subject_aliases)
from ..db import current_season, fmt_score
from ..excel_io import export_xlsx, import_into
from .detail_panel import DetailPanel
from .covers import CoverCache
from .dialogs import OptionsDialog, SettingsDialog, TextDialog
from .match_dialog import MatchDialog
from .poster_wall import PosterWall
from .refresh_dialog import RefreshDialog
from .search_dialog import SearchDialog
from .season_util import CURRENT_COLOR, FUTURE_COLOR, season_tag
from .style import apply_appearance
from .table_view import AnimeTable
from .table_model import (ALL_COLS, BACKLOG_COLS, ID_ROLE, RANK_COLS, SEASON_COLS, AnimeFilter,
                          AnimeModel)
from .workers import run_async

log = logging.getLogger("bangumi_recorder.ui")
# 选择导入文件时默认打开的文件夹：上次导入用的文件夹；第一次用时是项目根目录（data 文件夹的上一级）
PROJECT_DIR = config.DATA_DIR.parent
COL_WIDTHS = {"rank": 48, "title": 260, "romaji": 230, "score": 60, "tier": 100, "season": 110, "format": 78,
              "studio": 130, "tags": 170, "status": 74, "bgm_score": 74, "weekday": 48,
              "director": 100, "series_comp": 100, "short_name": 90, "episodes": 50,
              "air_date": 92, "comment": 160, "bgm": 44}
VIEW_RANK, VIEW_ALL, VIEW_BACKLOG, VIEW_SEASON = "rank", "all", "backlog", "season"
MONTH_NAME = {"01": "1 月 · 冬", "04": "4 月 · 春", "07": "7 月 · 夏", "10": "10 月 · 秋"}


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._client = None
        self._anilist = None
        self.view = (VIEW_RANK, None)
        self.rows = []
        self.covers = CoverCache(self.client, self)
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self._place_window()

        self._build_toolbar()

        self.nav = QTreeWidget()
        self.nav.setObjectName("nav")
        self.nav.setHeaderHidden(True)
        self.nav.setIndentation(0)
        self.nav.setRootIsDecorated(False)
        self.nav.setMinimumWidth(140)
        self.nav.itemSelectionChanged.connect(self._on_nav)

        self.model = AnimeModel(self)
        self.proxy = AnimeFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = AnimeTable()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().setDefaultSectionSize(self._row_height())
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.doubleClicked.connect(lambda _i: self._focus_score())
        self.table.clicked.connect(lambda _i: self.open_detail())
        self.table.field_chosen.connect(self.set_field)
        self.table.can_pick = lambda rid: not (
            self.detail.is_dirty() and self.detail.row and self.detail.row["id"] != rid)

        # 海报墙：和表格共用同一个模型和选择
        self.wall = PosterWall(self.covers)
        self.wall.setModel(self.proxy)
        self.wall.setSelectionModel(self.table.selectionModel())
        self.wall.doubleClicked.connect(lambda _i: self._focus_score())
        self.wall.clicked.connect(lambda _i: self.open_detail())
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.wall)

        self.detail = DetailPanel(self.client, db=self.db)
        self.detail.save_requested.connect(self.save_row)
        self.detail.save_close_requested.connect(self.save_and_close)
        self.detail.close_requested.connect(self.close_detail)
        self.detail.delete_requested.connect(self.delete_row)
        self.detail.link_requested.connect(self.link_row)
        self.detail.refresh_requested.connect(self.refresh_row)

        split = QSplitter()
        split.addWidget(self.nav)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self._build_filter_bar())
        cv.addWidget(self.stack, 1)
        split.addWidget(center)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 860, 390])
        self.split = split
        self._restore_splitter()
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        self.status = QLabel()
        self.statusBar().addWidget(self.status)

        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus)
        QShortcut(QKeySequence("Ctrl+N"), self, self.add_anime)
        QShortcut(QKeySequence("Ctrl+S"), self, self.detail.save)
        for view in (self.table, self.wall):
            QShortcut(QKeySequence(Qt.Key_Delete), view,
                      lambda: self.detail.row and self.delete_row(self.detail.row["id"]))

        self.set_layout(self.db.get_setting("layout") or "wall", save=False)
        self.reload()
        if not self.db.count() and self.db.get_setting("first_run_done") != "1":
            QTimer.singleShot(300, self._first_run)

    # ---------- 工具栏 ----------
    def _build_toolbar(self):
        tb = QToolBar("main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, slot, tip="", primary=False):
            a = QAction(text, self)
            a.setToolTip(tip or text)
            a.triggered.connect(slot)
            tb.addAction(a)
            if primary:
                tb.widgetForAction(a).setObjectName("primary")
            return a

        act("＋ 添加作品", self.add_anime, "从 Bangumi 搜索并添加 (Ctrl+N)；可以按季度、体裁、题材筛选", primary=True)
        act("季度新番", self.open_season_browser, "从 Bangumi 一次拉下一整季的新番，勾选后批量加进季度表")
        act("批量关联 Bangumi", self.batch_match, "为还没关联的作品自动查找 Bangumi 条目")
        act("刷新 Bangumi 数据", self.refresh_all, "重新获取已关联作品的 Bangumi 信息（季度、评分、封面等）")
        act("补全罗马音", self.fill_romaji, "先在 Bangumi 别名里找，再查 AniList；都没有的按读音自动转写并标注")
        tb.addSeparator()
        act("导入 Excel", self.import_excel, "从 Excel 记录表导入（只读取，不修改原文件）")
        act("导出 Excel", self.export_excel)
        tb.addSeparator()
        act("评分标准与选项", self.open_options, "评分标准（名作之壁）、观看状态、体裁——都是可以改的配置")
        act("设置", self.open_settings)

        # 搜索 / 筛选 / 视图切换放在内容区上方的一条筛选栏里（见 _build_filter_bar）

    def _build_filter_bar(self):
        bar = QWidget()
        bar.setObjectName("filterBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("搜索 名称 / 罗马音 / 公司 / 题材 / 评价…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self._filter(text=t))
        self.f_format = QComboBox()
        self.f_format.currentIndexChanged.connect(
            lambda i: self._filter(fmt=self.f_format.currentText() if i > 0 else ""))
        self.f_status = QComboBox()
        self.f_status.currentIndexChanged.connect(
            lambda i: self._filter(status=self.f_status.currentText() if i > 0 else ""))
        self._fill_filter_options()
        h.addWidget(self.search, 1)
        h.addWidget(self.f_format)
        h.addWidget(self.f_status)
        h.addSpacing(8)

        seg = QWidget()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.layout_buttons = {}
        group = QButtonGroup(self)
        for key, text in (("wall", "▦ 海报"), ("table", "☰ 列表")):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.set_layout(k))
            group.addButton(b)
            sl.addWidget(b)
            self.layout_buttons[key] = b
        h.addWidget(seg)
        return bar

    def set_layout(self, key, save=True):
        """切换 海报墙 / 列表。"""
        key = key if key in ("wall", "table") else "wall"
        self.layout_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.wall if key == "wall" else self.table)
        if save:
            self.db.set_setting("layout", key)
        cur = self.table.selectionModel().currentIndex()
        if cur.isValid():
            (self.wall if key == "wall" else self.table).scrollTo(cur)

    def _filter(self, **kw):
        self.proxy.set_filters(**kw)
        self._update_status()

    # ---------- 客户端 ----------
    def client(self):
        if self._client is None:
            self._client = BangumiClient(self.db.get_setting("bgm_token") or None)
        return self._client

    def known_studios(self):
        """你表里出现过的制作公司写法（同一家公司取最常用的写法）。"""
        from collections import Counter
        cnt = Counter(r["studio"] for r in self.db.all() if r.get("studio") and "/" not in r["studio"])
        known = {}
        for name, _ in cnt.most_common():
            known.setdefault(norm_key(name), name)
        return known

    def anilist(self):
        if self._anilist is None:
            self._anilist = AniListClient()
        return self._anilist

    # ---------- 数据刷新 ----------
    def reload(self, select_id=None):
        self.rows = self.db.all()
        self._build_nav()
        self._apply_view(select_id)
        # 详情面板如果没有未保存的修改，就换成数据库里的最新内容（后台可能刚更新过这一行）
        if self.detail.row and not self.detail.is_dirty():
            fresh = self.db.get(self.detail.row["id"])
            if fresh != self.detail.row:
                self.detail.show_row(fresh)

    def _build_nav(self):
        self.nav.blockSignals(True)
        self.nav.clear()
        n_all = len(self.rows)
        n_scored = sum(1 for r in self.rows if r.get("score") is not None)
        n_back = sum(1 for r in self.rows if r.get("is_backlog"))

        def item(parent, text, key, bold=False):
            it = QTreeWidgetItem(parent, [text])
            it.setData(0, Qt.UserRole, key)
            if bold:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            return it

        item(self.nav, f"🏆  总榜  ({n_scored})", (VIEW_RANK, None), True)
        item(self.nav, f"📚  全部作品  ({n_all})", (VIEW_ALL, None), True)
        item(self.nav, f"🕘  补番  ({n_back})", (VIEW_BACKLOG, None), True)
        head = QTreeWidgetItem(self.nav, ["📅  季度"])
        head.setFlags(Qt.ItemIsEnabled)
        f = head.font(0)
        f.setBold(True)
        head.setFont(0, f)
        years = {}
        seasons = self.db.seasons()
        cur = current_season()                 # 按电脑的本机日期
        if cur not in {s for s, _ in seasons}:  # 当季还没有作品也列出来，方便点进去拉新番
            seasons = sorted(seasons + [(cur, 0)], key=lambda x: x[0], reverse=True)
        for season, n in seasons:
            if len(season) == 4 and season.isdigit():
                y = f"20{season[:2]}"
                if y not in years:
                    years[y] = QTreeWidgetItem(head, ["   " + y])
                    years[y].setFlags(Qt.ItemIsEnabled)
                label = MONTH_NAME.get(season[2:], season[2:] + " 月")
                tag = season_tag(season)
                mark = {"当季": "●", "未开播": "○"}.get(tag, " ")
                it = item(years[y], f"    {mark}  {label}  ({n})", (VIEW_SEASON, season))
                if tag:
                    it.setForeground(0, CURRENT_COLOR if tag == "当季" else FUTURE_COLOR)
                    f = it.font(0)
                    f.setBold(tag == "当季")
                    it.setFont(0, f)
                    it.setToolTip(0, "● 当季：按电脑的日期，这是正在播出的季度" if tag == "当季"
                                  else "○ 未开播：已经定档、还没开播的季度")
            else:
                item(head, f"   {season}  ({n})", (VIEW_SEASON, season))
        self.nav.expandAll()
        # 恢复选中
        target = None
        it = self.nav.invisibleRootItem()
        stack = [it.child(i) for i in range(it.childCount())]
        while stack:
            x = stack.pop()
            key = x.data(0, Qt.UserRole)
            if key and tuple(key) == self.view:
                target = x
            stack.extend(x.child(i) for i in range(x.childCount()))
        if target is None:
            self.view = (VIEW_RANK, None)
            target = self.nav.topLevelItem(0)
        self.nav.setCurrentItem(target)
        self.nav.blockSignals(False)

    def _on_nav(self):
        it = self.nav.currentItem()
        key = it.data(0, Qt.UserRole) if it else None
        if not key or tuple(key) == self.view:
            return
        if not self._confirm_leave():
            QTimer.singleShot(0, self._build_nav)
            return
        self.view = tuple(key)
        self._apply_view()

    def _apply_view(self, select_id=None):
        kind, season = self.view
        if kind == VIEW_RANK:
            rows, cols = [r for r in self.rows if r.get("score") is not None], RANK_COLS
        elif kind == VIEW_ALL:
            rows, cols = list(self.rows), ALL_COLS
        elif kind == VIEW_BACKLOG:
            rows, cols = [r for r in self.rows if r.get("is_backlog")], BACKLOG_COLS
        else:
            rows = [r for r in self.rows if not r.get("is_backlog") and r.get("season") == season]
            cols = SEASON_COLS
        self.table.setSortingEnabled(False)
        self.table.set_columns(cols)
        self.model.set_data(rows, cols)
        k = QApplication.instance().font().pointSizeF() / 10
        for i, c in enumerate(cols):
            self.table.setColumnWidth(i, int(COL_WIDTHS.get(c, 90) * k))
        header = self.table.horizontalHeader()
        if kind == VIEW_RANK:
            header.setSortIndicator(cols.index("score"), Qt.DescendingOrder)
        elif kind == VIEW_ALL:
            header.setSortIndicator(cols.index("season"), Qt.DescendingOrder)
        else:
            header.setSortIndicator(-1, Qt.AscendingOrder)   # 季度表/补番：保持原表顺序
        self.table.setSortingEnabled(True)
        if kind not in (VIEW_RANK, VIEW_ALL):
            self.proxy.sort(-1)
        self._update_status()
        self.select_id(select_id)

    def select_id(self, anime_id):
        if anime_id is not None:
            for r in range(self.proxy.rowCount()):
                idx = self.proxy.index(r, 0)
                if idx.data(ID_ROLE) == anime_id:
                    self.table.selectionModel().setCurrentIndex(
                        idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                    self.table.scrollTo(idx)
                    self.wall.scrollTo(idx)
                    return
        self.table.clearSelection()
        self.detail.show_row(None)

    def _on_row(self, cur, _prev):
        if not cur.isValid():
            return
        rid = cur.data(ID_ROLE)
        if self.detail.row and self.detail.row["id"] == rid:
            return
        self.open_detail()
        if self.detail.is_dirty():
            # 有未保存的修改：等这次选中处理完再问「要保存吗」。
            # 不能在选中信号里直接保存——保存会让这一行换位置/被筛掉，
            # Qt 还拿着刚才点中的那一格，接着用就会闪退（暴力测试发现）。
            QTimer.singleShot(0, lambda: self._leave_to(rid))
            return
        self.detail.show_row(self.db.get(rid))

    def _leave_to(self, rid):
        if not self.detail.row or self.detail.row["id"] == rid:
            return
        keep = self.detail.row["id"]
        if not self._confirm_leave():
            self.select_id(keep)
            return
        if self.db.get(rid):
            self._goto(rid)
        else:
            self.reload()

    def _confirm_leave(self):
        if not self.detail.is_dirty():
            return True
        ans = QMessageBox.question(self, "未保存的修改",
                                   f"「{self.detail.row['title']}」有修改还没保存，要保存吗？",
                                   QMessageBox.Save | QMessageBox.Discard, QMessageBox.Save)
        if ans != QMessageBox.Save:
            self.detail._dirty = False
            return True
        row_id = self.detail.row["id"]
        if not self._commit(row_id, self.detail.collect()):
            return False
        # 就地刷新这一行。必须先发 layoutAboutToBeChanged 再改数据、最后发 layoutChanged，
        # 否则排序/筛选代理里的行映射会错乱，接下来一重绘就会闪退。
        fresh = self.db.get(row_id)
        self.model.layoutAboutToBeChanged.emit()
        for r in self.rows:
            if r["id"] == row_id:
                r.clear()
                r.update(fresh)
        self.model.layoutChanged.emit()
        old_view = self.view
        self._build_nav()
        if self.view != old_view:          # 这一季已经没有作品了（比如刚把季度删掉）
            QTimer.singleShot(0, self._apply_view)
        return True

    def _update_status(self):
        n = self.proxy.rowCount()
        scores = [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()].get("score")
                  for i in range(n)]
        scores = [s for s in scores if s is not None]
        valid = [s for s in scores if s >= 0]          # 负分（如 -1）不计入平均
        avg = f"{sum(valid) / len(valid):.2f}" if valid else "—"
        self.status.setText(f"本页 {n} 部 · 已评分 {len(scores)} 部 · 平均 {avg}    |    "
                            f"共 {len(self.rows)} 部作品")

    def _goto(self, row_id):
        """跳到某部作品所在的视图并选中它。"""
        r = self.db.get(row_id)
        if not r:
            self.reload()
            return
        kind, season = self.view
        visible = (kind == VIEW_ALL or (kind == VIEW_RANK and r.get("score") is not None)
                   or (kind == VIEW_BACKLOG and r.get("is_backlog"))
                   or (kind == VIEW_SEASON and not r.get("is_backlog") and r.get("season") == season))
        if not visible:
            if r.get("is_backlog"):
                self.view = (VIEW_BACKLOG, None)
            elif r.get("season"):
                self.view = (VIEW_SEASON, r["season"])
            else:
                self.view = (VIEW_ALL, None)
        self.reload(row_id)
        self.detail.show_row(self.db.get(row_id))

    # ---------- 增删改 ----------
    def _commit(self, row_id, data):
        """校验并写入数据库。成功返回 True。"""
        season = data.get("season")
        if season and not re.fullmatch(r"\d{2}(0[1-9]|1[0-2])|\d{2}年", season):
            QMessageBox.warning(self, "季度格式",
                                "季度请写成 YYMM（如 2507 = 2025 年 7 月），老番可以只写年份（如 16年），也可以留空。")
            return False
        dup = self.db.find_duplicate(data.get("title"), data.get("season"), exclude_id=row_id)
        if dup:
            QMessageBox.warning(self, "重复", f"同一季度里已经有「{data.get('title')}」了。")
            return False
        if data.get("air_date"):
            try:
                date.fromisoformat(data["air_date"])
            except ValueError:
                QMessageBox.warning(self, "日期格式", "开播时间请写成 yyyy-mm-dd，比如 2025-07-05。")
                return False
        # 只写入用户在面板里真正改过的字段：面板打开期间后台（批量关联、刷新、补全罗马音）
        # 可能已经更新了别的字段，整行写回会把那些更新覆盖掉。
        base = self.detail.row if self.detail.row and self.detail.row["id"] == row_id else self.db.get(row_id)
        changes = {k: v for k, v in data.items() if base is None or base.get(k) != v}
        try:
            if changes:
                self.db.update(row_id, changes)
        except ValueError as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return False
        self.detail._dirty = False
        return True

    def save_row(self, row_id, data):
        log.info("保存 #%s %s", row_id, data.get("title"))
        if self._commit(row_id, data):
            self._goto(row_id)
            self.statusBar().showMessage("已保存", 2500)

    def save_and_close(self, row_id, data):
        """侧边栏里按回车：保存，成功后关闭侧边栏。"""
        if self.detail.is_dirty():
            log.info("保存 #%s %s（回车）", row_id, data.get("title"))
            if not self._commit(row_id, data):
                return
            self._goto(row_id)
            self.statusBar().showMessage("已保存", 2500)
        self.detail.hide()

    # ---------- 侧边栏 开 / 关 ----------
    def open_detail(self):
        if self.detail.isHidden():
            self.detail.show()

    def close_detail(self):
        if not self._confirm_leave():
            return
        if self.detail.row:                     # 放弃了修改：面板内容恢复成数据库里的样子
            self.detail.show_row(self.db.get(self.detail.row["id"]))
        self.detail.hide()
        view = self.stack.currentWidget()
        if view.currentIndex().isValid():       # 没有选中时聚焦会自动选第一行、又把侧边栏打开
            view.setFocus()

    def _focus_score(self):
        self.open_detail()
        self.detail.w["score"].setFocus()
        self.detail.w["score"].selectAll()

    # ---------- 在表格里直接改状态 / 体裁 ----------
    def set_status(self, row_id, value):
        self.set_field(row_id, "status", value)

    def set_field(self, row_id, field, value):
        if field not in ("status", "format"):
            return
        r = self.db.get(row_id)
        if not r or r.get(field) == value:
            return
        log.info("%s #%s %s：%s → %s", field, row_id, r["title"], r.get(field), value)
        self.db.update(row_id, {field: value})
        fresh = self.db.get(row_id)
        for i, x in enumerate(self.model.rows):
            if x["id"] == row_id:
                x.update(fresh)
                self.model.dataChanged.emit(self.model.index(i, 0),
                                            self.model.index(i, self.model.columnCount() - 1))
        for x in self.rows:
            if x["id"] == row_id and x is not fresh:
                x.update(fresh)
        if self.detail.row and self.detail.row["id"] == row_id:
            if self.detail.is_dirty():
                # 右边正在编辑这部作品：只把这一格跟着改，别的修改保留
                self.detail.row[field] = value
                was, self.detail._loading = self.detail._loading, True
                self.detail.w[field].setCurrentText(value or "")
                self.detail._loading = was
            else:
                self.detail.show_row(fresh)
        self._update_status()
        what = "状态" if field == "status" else "体裁"
        self.statusBar().showMessage(f"「{r['title']}」{what}：{r.get(field) or '（空）'} → {value or '（空）'}", 4000)

    # ---------- 评分标准与选项 ----------
    def _fill_filter_options(self):
        cfg = appconfig.current()
        for combo, first, items, extra in ((self.f_format, "全部体裁", cfg.formats(), []),
                                           (self.f_status, "全部状态", cfg.status_names(), ["（空）"])):
            cur = combo.currentText() if combo.currentIndex() > 0 else None
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([first] + items + extra)
            i = combo.findText(cur) if cur else 0
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)
            if cur and i < 0:                   # 原来筛选的那一项被删掉了
                self._filter(**{"fmt" if combo is self.f_format else "status": ""})

    def open_options(self):
        if not self._confirm_leave():
            return
        self.db.cfg.load()                      # 可能刚用记事本改过 config.json
        dlg = OptionsDialog(self.db, self)
        dlg.exec()
        self.apply_options()

    def apply_options(self):
        """配置文件改了：下拉选项、评级、颜色全部按新配置重画。"""
        self._fill_filter_options()
        self.detail.reload_options()
        self.reload(self.detail.row["id"] if self.detail.row else None)
        self.table.viewport().update()
        self.wall.viewport().update()

    # ---------- 季度新番 ----------
    def open_season_browser(self):
        if not self._confirm_leave():
            return
        from ..db import is_quarter
        from .season_dialog import SeasonDialog
        kind, season = self.view
        start = season if kind == VIEW_SEASON and is_quarter(season) else current_season()
        log.info("打开季度新番 %s", start)
        dlg = SeasonDialog(self.db, self.client(), self.known_studios(), season=start, parent=self)
        dlg.exec()
        added, target = list(dlg.added_ids), dlg.added_season or start
        dlg.deleteLater()                       # 里面存着整季的数据，关掉就释放
        if added:
            self.view = (VIEW_SEASON, target)
            self.reload(added[0])
            self.statusBar().showMessage(f"已添加 {len(added)} 部到「{target}」", 5000)

    def delete_row(self, row_id):
        r = self.db.get(row_id)
        if not r:
            return
        log.info("请求删除 #%s %s", row_id, r.get("title"))
        extra = f"\n评分 {fmt_score(r['score'])} 也会一起删除。" if r.get("score") is not None else ""
        if QMessageBox.question(self, "删除", f"确定删除「{r['title']}」？{extra}") != QMessageBox.Yes:
            return
        self.db.delete(row_id)
        self.detail._dirty = False
        self.detail.show_row(None)
        self.reload()

    def add_anime(self):
        if not self._confirm_leave():
            return
        kind, season = self.view
        dlg = SearchDialog(self.client(), mode="add", default_backlog=kind == VIEW_BACKLOG, parent=self)
        if not dlg.exec():
            return
        backlog = int(dlg.backlog)
        if dlg.manual:
            data = {"title": "新作品", "is_backlog": backlog,
                    "season": season if kind == VIEW_SEASON else None}
            n = 2
            while self.db.find_duplicate(data["title"], data["season"]):
                data["title"] = f"新作品 {n}"
                n += 1
            rid = self.db.add(data)
            self._goto(rid)
            self.detail.w["title"].setFocus()
            self.detail.w["title"].selectAll()
            return
        subj = dlg.selected
        same = [r for r in self.rows if r.get("bgm_id") == subj.get("id")]
        if same and QMessageBox.question(
                self, "已经有了", f"「{same[0]['title']}」已经关联了这个条目，还要再添加一次吗？") != QMessageBox.Yes:
            self._goto(same[0]["id"])
            return
        self.statusBar().showMessage("正在从 Bangumi 获取详情和罗马音…")
        known = self.known_studios()
        client, anilist = self.client(), self.anilist()      # 在主线程里取
        run_async(lambda: full_details(client, subj["id"], anilist, known),
                  lambda fields: self._add_from_bangumi(fields, backlog),
                  self._net_error)

    def _add_from_bangumi(self, fields, backlog):
        log.info("添加作品：Bangumi #%s %s", fields.get("bgm_id"), fields.get("title"))
        self.statusBar().clearMessage()
        data = {k: v for k, v in fields.items() if v is not None}
        data["is_backlog"] = backlog
        if fields.get("romaji"):
            data["extra"] = with_romaji_source({}, fields.get("romaji_source"))
        existing = self.db.find_duplicate(data["title"], data.get("season"))
        if existing:
            old = self.db.get(existing)
            if not old.get("bgm_id") and QMessageBox.question(
                    self, "已有同名记录",
                    f"「{old['title']}」（{old.get('season') or '季度未知'}）已经在表里了，"
                    "要把这个 Bangumi 条目关联到它吗？\n\n选「否」则另外新建一条。") == QMessageBox.Yes:
                self._merge(existing, fields, relink=True)
                return
        base, n = data["title"], 2
        while self.db.find_duplicate(data["title"], data.get("season")):
            data["title"] = f"{base} ({n})"
            n += 1
        rid = self.db.add(data)
        self._goto(rid)
        self.detail.w["score"].setFocus()
        self.statusBar().showMessage(f"已添加「{data['title']}」，可以直接填写评分", 4000)

    def link_row(self, row_id):
        if not self._confirm_leave():
            return
        r = self.db.get(row_id)
        dlg = SearchDialog(self.client(), keyword=r["title"], mode="link", parent=self, season=r.get("season"))
        if not dlg.exec() or not dlg.selected:
            return
        sid = dlg.selected["id"]
        self.statusBar().showMessage("正在从 Bangumi 获取详情和罗马音…")
        known = self.known_studios()
        client, anilist = self.client(), self.anilist()      # 在主线程里取
        run_async(lambda: full_details(client, sid, anilist, known),
                  lambda fields: self._merge(row_id, fields, relink=True), self._net_error)

    def refresh_row(self, row_id):
        r = self.db.get(row_id)
        if not r or not r.get("bgm_id"):
            return
        self.statusBar().showMessage("正在从 Bangumi 获取详情和罗马音…")
        known = self.known_studios()
        client, anilist = self.client(), self.anilist()      # 在主线程里取
        run_async(lambda: full_details(client, r["bgm_id"], anilist, known),
                  lambda fields: self._merge(row_id, fields), self._net_error)

    def _merge(self, row_id, fields, relink=False):
        log.info("合并 Bangumi #%s 到 #%s（relink=%s）", fields.get("bgm_id"), row_id, relink)
        self.statusBar().clearMessage()
        r = self.db.get(row_id)
        if not r:
            return
        # 重新关联 / 刷新：播出季度和开播时间以 Bangumi 条目为准，其他个人字段只补空白
        update = merge_fields(r, fields, fill_only_blank=True, sync_broadcast=True)
        for bid in (r.get("bgm_id"), fields.get("bgm_id")):
            if bid:
                self.covers.forget(bid)
        if relink and r.get("bgm_id") and r["bgm_id"] != fields.get("bgm_id"):
            # 换了条目：旧条目的封面/简介/罗马音不再适用
            for k in ("bgm_name", "bgm_name_cn", "bgm_score", "bgm_rank", "cover_url", "summary"):
                update.setdefault(k, None)
            update["romaji"] = fields.get("romaji")
            update["extra"] = with_romaji_source(r, fields.get("romaji_source"))
        self.db.update(row_id, update)
        self._goto(row_id)
        parts = ["已更新 Bangumi 信息"]
        if "season" in update:
            parts.append(f"季度 {r.get('season') or '（空）'} → {update['season']}")
        filled = [k for k in update if k not in ("season", "air_date", "extra", "cover_url", "summary")
                  and not k.startswith("bgm_")]
        if filled:
            parts.append(f"补全了 {len(filled)} 个空白字段")
        self.statusBar().showMessage("，".join(parts), 6000)

    def _net_error(self, err):
        log.warning("Bangumi 请求失败：%s", (err or "").strip().splitlines()[-1:] or err)
        self.statusBar().clearMessage()
        err = err or "未知错误"
        if "Traceback" in err:          # 程序内部错误：给完整信息，方便排查
            last = err.strip().splitlines()[-1]
            errors.show_error_dialog(self, "出错了", last, err)
        else:                           # 网络/Bangumi 返回的错误：一句话说明就够了
            QMessageBox.warning(self, "Bangumi 请求失败",
                                err.strip().splitlines()[-1] + "\n\n请检查网络后再试。")

    def batch_match(self):
        log.info("打开批量关联")
        if not self._confirm_leave():
            return
        todo = [r for r in self.db.all() if not r.get("bgm_id")]
        if not todo:
            QMessageBox.information(self, "批量关联", "所有作品都已经关联了 Bangumi。")
            return
        dlg = MatchDialog(self.db, self.client(), todo, self, known_studios=self.known_studios())
        dlg.exec()
        if dlg.changed:
            cur = self.detail.row["id"] if self.detail.row else None
            self.reload(cur)
            if QMessageBox.question(self, "罗马音", "要接着为新关联的作品补全罗马音吗？") == QMessageBox.Yes:
                self.fill_romaji()

    # ---------- 一键刷新 ----------
    def refresh_all(self):
        if not self._confirm_leave():
            return
        log.info("打开刷新 Bangumi 数据")
        it = self.nav.currentItem()
        view_name = it.text(0).split("(")[0].strip() if it else "当前页面"
        dlg = RefreshDialog(self.db, self.client(), self.db.all(), list(self.model.rows),
                            self.known_studios(), view_name, self)
        dlg.exec()
        if dlg.changed_ids:
            self.covers.mem.clear()
            self.covers.failed.clear()
            cur = self.detail.row["id"] if self.detail.row else None
            self.reload(cur)
            if cur:
                self.detail.show_row(self.db.get(cur))

    # ---------- 罗马音 ----------
    def fill_romaji(self):
        log.info("开始补全罗马音")
        if not self._confirm_leave():
            return
        all_rows = self.db.all()
        rows = [r for r in all_rows if r.get("bgm_name") and (
            not r.get("romaji") or extra_of(r).get("罗马音来源") == SOURCE_KAKASI)]
        no_name = sum(1 for r in all_rows if not r.get("bgm_name") and not r.get("romaji"))
        if not rows:
            msg = "所有有日文原名的作品都已经有罗马音了。"
            if no_name:
                msg += f"\n\n另有 {no_name} 部还没关联 Bangumi（没有日文原名），关联后再补。"
            QMessageBox.information(self, "补全罗马音", msg)
            return
        self.db.backup("before_romaji")
        n = len(rows)
        prog = QProgressDialog(f"正在查 {n} 部作品的罗马音…", "停止", 0, n * 2, self)
        prog.setWindowTitle("补全罗马音")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        client, anilist = self.client(), self.anilist()

        def job(report, cancelled):
            # 第一步：Bangumi 条目的「别名」里找罗马音
            found, rest = {}, []
            for i, r in enumerate(rows, 1):
                if cancelled():
                    return found, []
                rj = None
                if r.get("bgm_id"):
                    try:
                        rj = from_aliases(r["bgm_name"], subject_aliases(client.subject(r["bgm_id"])))
                    except Exception:  # noqa: BLE001
                        rj = None
                if rj:
                    found[r["id"]] = (rj, SOURCE_BANGUMI)
                else:
                    rest.append((r["id"], r["bgm_name"], r.get("air_date")))
                report(i, n * 2, f"第 1 步：在 Bangumi 别名里找罗马音（{i}/{n}）")
            # 第二步：剩下的查 AniList，再查不到就自动转写
            more, failed = lookup_many(
                rest, anilist, cancelled=cancelled,
                report=lambda d, t: report(n + int(d / max(t, 1) * n), n * 2,
                                           f"第 2 步：查 AniList（{d}/{t}）"))
            found.update(more)
            return found, failed

        def progress(done, total, label):
            prog.setValue(done)
            if label:
                prog.setLabelText(label)

        task = run_async(job, lambda res: self._romaji_done(res, rows, no_name, prog),
                         lambda err: (prog.close(), self._net_error(err)), progress)
        prog.canceled.connect(lambda: setattr(task, "cancelled", True))

    def _romaji_done(self, res, rows, no_name, prog):
        prog.close()
        found, failed = res
        by_id = {r["id"]: r for r in rows}
        n_bgm = n_ani = n_kks = 0
        with self.db.conn:
            for rid, (rj, src) in found.items():
                r = by_id[rid]
                was_guess = extra_of(r).get("罗马音来源") == SOURCE_KAKASI
                if r.get("romaji") and not was_guess:
                    continue
                if was_guess and src not in (SOURCE_ANILIST, SOURCE_BANGUMI):
                    continue        # 之前就是转写，这次还是没查到 → 保持原样
                self.db.update(rid, {"romaji": rj, "extra": with_romaji_source(r, src)}, commit=False)
                if src == SOURCE_BANGUMI:
                    n_bgm += 1
                elif src == SOURCE_ANILIST:
                    n_ani += 1
                else:
                    n_kks += 1
        cur = self.detail.row["id"] if self.detail.row else None
        self.reload(cur)
        if cur:
            self.detail.show_row(self.db.get(cur))
        lines = [f"从 Bangumi 别名里找到 {n_bgm} 部，从 AniList 查到 {n_ani} 部。"]
        if n_kks:
            lines.append(f"{n_kks} 部在 AniList 上没查到，已按日文读音自动转写（详情里有标注，建议核对）。")
        if failed:
            lines.append(f"{len(failed)} 部因为网络问题没查成，可以稍后再点一次「补全罗马音」。")
        if no_name:
            lines.append(f"另有 {no_name} 部还没关联 Bangumi，没有日文原名。")
        QMessageBox.information(self, "补全罗马音", "\n".join(lines))

    # ---------- 导入 / 导出 ----------
    def _first_run(self):
        """第一次启动：让用户选一个 Excel 记录表导入；不选就从空白开始（以后不再弹出）。"""
        box = QMessageBox(self)
        box.setWindowTitle("欢迎使用 Bangumi Recorder")
        box.setText("数据库还是空的。\n\n如果你之前用 Excel 记录过看过的动画，可以选择那个表格导入；"
                    "也可以先从空白开始，以后随时点工具栏的「导入 Excel」。\n\n导入只读取 Excel，不会修改原文件。")
        pick = box.addButton("选择要导入的 Excel…", QMessageBox.AcceptRole)
        box.addButton("从空白开始", QMessageBox.RejectRole)
        box.setDefaultButton(pick)
        box.exec()
        self.db.set_setting("first_run_done", "1")
        if box.clickedButton() is pick:
            self.import_excel()

    def _import_start_dir(self):
        last = self.db.get_setting("last_import_dir")
        if last and Path(last).is_dir():
            return last
        return str(PROJECT_DIR if PROJECT_DIR.is_dir() else Path.home())

    def import_excel(self):
        log.info("导入 Excel")
        if not self._confirm_leave():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择要导入的 Excel 记录表", self._import_start_dir(),
                                              "Excel 文件 (*.xlsx)")
        if not path:
            return
        self.db.set_setting("last_import_dir", str(Path(path).parent))
        replace = False
        if self.db.count():
            ans = QMessageBox.question(
                self, "覆盖导入",
                f"数据库里已经有 {self.db.count()} 部作品。\n\n"
                "继续会先自动备份当前数据库，然后用 Excel 的内容替换全部数据"
                "（包括你在 app 里做的修改和 Bangumi 关联）。确定吗？")
            if ans != QMessageBox.Yes:
                return
            replace = True
        try:
            report = import_into(self.db, path, replace=replace)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(e))
            return
        self.db.set_setting("last_import_file", str(Path(path).resolve()))
        self.view = (VIEW_RANK, None)
        self.reload()
        TextDialog("导入完成", report.text() +
                   "\n\n下一步：点工具栏的「批量关联 Bangumi」，自动补全封面和制作信息。", self).exec()

    def export_excel(self):
        log.info("导出 Excel")
        folder = self.db.get_setting("last_export_dir")
        if not (folder and Path(folder).is_dir()):
            folder = str(Path.home() / "Desktop")
        default = Path(folder) / f"看番记录_导出_{date.today():%Y%m%d}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", str(default), "Excel 文件 (*.xlsx)")
        if not path:
            return
        imported = self.db.get_setting("last_import_file")
        if imported and os.path.normcase(str(Path(path).resolve())) == os.path.normcase(imported):
            if QMessageBox.question(self, "注意", "这就是你当初导入的那个表格，导出会覆盖它。确定吗？") \
                    != QMessageBox.Yes:
                return
        self.db.set_setting("last_export_dir", str(Path(path).parent))
        try:
            export_xlsx(self.db.all(), self.db.rubric(), path)
        except PermissionError:
            QMessageBox.warning(self, "导出失败", "文件被占用（可能正在 Excel 里打开），请关掉后再试。")
            return
        self.statusBar().showMessage(f"已导出到 {path}", 5000)

    def _row_height(self):
        return int(QApplication.instance().font().pointSizeF() * 3.0) + 2

    def apply_font_size(self, pt, font_key=None):
        apply_appearance(QApplication.instance(), pt, font_key)
        self.table.verticalHeader().setDefaultSectionSize(self._row_height())
        self.wall.refresh_fonts(QApplication.instance().font())
        self._apply_view(self.detail.row["id"] if self.detail.row else None)

    def open_settings(self):
        old_pt = self.db.get_setting("font_pt")
        old_font = self.db.get_setting("font_family")
        dlg = SettingsDialog(self.db, self)
        ok = dlg.exec()
        if dlg.restored:                      # 从备份恢复过：整个界面重新读数据
            log.info("已从备份恢复数据库")
            self.covers.mem.clear()
            self.detail._dirty = False
            self.detail.show_row(None)
            self.reload()
        if ok:
            self._client = None   # token 可能改了
            if self.db.get_setting("font_pt") != old_pt or self.db.get_setting("font_family") != old_font:
                self.apply_font_size(int(self.db.get_setting("font_pt") or 11),
                                     self.db.get_setting("font_family") or "system")

    # ---------- 窗口位置 ----------
    def _place_window(self):
        """恢复上次的窗口位置；如果那个位置已经不在任何屏幕上（换了显示器、分辨率、缩放），
        就按当前屏幕大小重新摆在中间。窗口不会再大过屏幕、跑到屏幕外面去。"""
        import base64
        from PySide6.QtCore import QByteArray, QRect
        raw = self.db.get_setting("window_geometry")
        if raw:
            try:
                if self.restoreGeometry(QByteArray(base64.b64decode(raw))) and self._on_screen():
                    if self.db.get_setting("window_maximized") == "1":
                        QTimer.singleShot(0, self.showMaximized)
                    return
            except (ValueError, TypeError):
                pass
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1440, 880)
        w = min(1440, int(avail.width() * 0.92))
        h = min(900, int(avail.height() * 0.9))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
        if avail.width() < 1280 or avail.height() < 760:      # 小屏幕：直接最大化
            QTimer.singleShot(0, self.showMaximized)

    def _on_screen(self):
        """标题栏的大部分要在某个屏幕的可用区域里，窗口也不能比那个屏幕大。"""
        from PySide6.QtCore import QRect
        frame = self.frameGeometry()
        title = QRect(frame.x(), frame.y(), frame.width(), 40)
        for sc in QApplication.screens():
            avail = sc.availableGeometry()
            inter = avail.intersected(title)
            if (inter.width() * inter.height() >= title.width() * title.height() * 0.6
                    and frame.width() <= avail.width() + 16 and frame.height() <= avail.height() + 16):
                return True
        return False

    def _restore_splitter(self):
        import base64
        from PySide6.QtCore import QByteArray
        raw = self.db.get_setting("splitter_state")
        if raw:
            try:
                self.split.restoreState(QByteArray(base64.b64decode(raw)))
            except (ValueError, TypeError):
                pass

    def _save_window(self):
        import base64
        try:
            self.db.set_setting("window_maximized", "1" if self.isMaximized() else "0")
            self.db.set_setting("window_geometry", base64.b64encode(bytes(self.saveGeometry())).decode())
            self.db.set_setting("splitter_state", base64.b64encode(bytes(self.split.saveState())).decode())
        except Exception:  # noqa: BLE001  记不住位置不影响退出
            log.exception("保存窗口位置失败")

    def closeEvent(self, e):
        if not self._confirm_leave():
            e.ignore()
            return
        self._save_window()
        super().closeEvent(e)
