"""季度新番：从 Bangumi 一次拉下某一季开播的全部动画，筛选、勾选后批量加进季度表。

默认打开「当季」（按电脑的本机日期算）；也可以选已经定档、还没开播的未来季度。
"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout)

from .. import appconfig
from ..bangumi import (guess_format, is_adult, subject_region, subject_tag_names, with_romaji_source)
from ..db import current_season, season_from_date, season_label, shift_season
from ..romaji import SOURCE_BANGUMI, from_aliases
from .season_util import CURRENT_COLOR, FUTURE_COLOR, season_choices, season_tag
from .workers import run_async

log = logging.getLogger("bangumi_recorder.ui")
PLATFORMS = ["TV", "WEB", "剧场版", "OVA", "其他"]
REGIONS = ["全部地区", "日本", "国产", "欧美", "韩国", "其他"]
COLS = ["", "名称", "开播", "平台", "体裁", "集数", "题材", "Bangumi", "表里"]
C_CHECK, C_NAME, C_DATE, C_PLAT, C_FMT, C_EPS, C_TAGS, C_SCORE, C_HAVE = range(len(COLS))


def platform_of(subj):
    p = (subj.get("platform") or "").upper()
    if p == "TV":
        return "TV"
    if p == "WEB":
        return "WEB"
    if "剧场" in p or p == "MOVIE":
        return "剧场版"
    if p in ("OVA", "OAD"):
        return "OVA"
    return "其他"


class _SortItem(QTableWidgetItem):
    """按 UserRole+1 里的值排序（数字列按数字排）。"""

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


class SeasonDialog(QDialog):
    def __init__(self, db, client, known_studios=None, season=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.client = client
        self.known = known_studios or {}
        self.cache = {}                    # 季度 → 条目列表（这次打开窗口期间不重复拉）
        self.task = None
        self.added_ids = []                # 加进表里的作品 id（主窗口用来跳过去）
        self.added_season = None
        self._fetch_gen = 0
        self.setWindowTitle("季度新番")
        self.resize(1080, 700)

        v = QVBoxLayout(self)
        # ---- 季度选择 ----
        top = QHBoxLayout()
        top.addWidget(QLabel("季度："))
        self.prev_btn = QToolButton()
        self.prev_btn.setText("◀")
        self.prev_btn.setToolTip("上一季")
        self.next_btn = QToolButton()
        self.next_btn.setText("▶")
        self.next_btn.setToolTip("下一季")
        self.season_box = QComboBox()
        self.season_box.setMinimumWidth(200)
        self._fill_seasons(season or current_season())
        self.tag = QLabel()
        self.tag.setObjectName("seasonTag")
        self.reload_btn = QPushButton("重新获取")
        self.reload_btn.setToolTip("Bangumi 上的新番信息会陆续补全，可以过段时间再拉一次")
        top.addWidget(self.prev_btn)
        top.addWidget(self.season_box)
        top.addWidget(self.next_btn)
        top.addWidget(self.tag)
        top.addStretch()
        top.addWidget(self.reload_btn)
        v.addLayout(top)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.season_box.currentIndexChanged.connect(lambda _i: self.fetch())
        self.reload_btn.clicked.connect(lambda: self.fetch(force=True))

        # ---- 筛选 ----
        fl = QHBoxLayout()
        self.plat = {}
        for p in PLATFORMS:
            b = QCheckBox(p)
            b.setChecked(p != "其他")
            b.toggled.connect(lambda _c: self.apply_filter())
            self.plat[p] = b
            fl.addWidget(b)
        fl.addSpacing(10)
        self.region = QComboBox()
        self.region.addItems(REGIONS)
        self.region.setCurrentText("日本")
        self.region.currentIndexChanged.connect(lambda _i: self.apply_filter())
        self.fmt = QComboBox()
        self.fmt.addItem("任意体裁", None)
        for f in appconfig.current().formats():
            self.fmt.addItem(f, f)
        self.fmt.currentIndexChanged.connect(lambda _i: self.apply_filter())
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("题材，如 恋爱 / 漫画改 / MAPPA")
        self.tags.setClearButtonEnabled(True)
        self.tags.textChanged.connect(lambda _t: self.apply_filter())
        self.kw = QLineEdit()
        self.kw.setPlaceholderText("名称里包含…")
        self.kw.setClearButtonEnabled(True)
        self.kw.textChanged.connect(lambda _t: self.apply_filter())
        fl.addWidget(self.region)
        fl.addWidget(self.fmt)
        fl.addWidget(self.tags, 1)
        fl.addWidget(self.kw, 1)
        v.addLayout(fl)
        fl2 = QHBoxLayout()
        self.hide_adult = QCheckBox("隐藏 R18")
        self.hide_adult.setChecked(True)
        self.hide_have = QCheckBox("隐藏已经在表里的")
        for b in (self.hide_adult, self.hide_have):
            b.toggled.connect(lambda _c: self.apply_filter())
            fl2.addWidget(b)
        fl2.addStretch()
        self.info = QLabel()
        self.info.setObjectName("subtitle")
        fl2.addWidget(self.info)
        v.addLayout(fl2)

        # ---- 列表 ----
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("seasonTable")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for c, w in ((C_CHECK, 34), (C_NAME, 330), (C_DATE, 96), (C_PLAT, 64), (C_FMT, 72), (C_EPS, 54),
                     (C_TAGS, 220), (C_SCORE, 74), (C_HAVE, 70)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._toggle_row)
        self.table.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.table, 1)

        # ---- 底部 ----
        bl = QHBoxLayout()
        all_btn = QPushButton("全选（当前筛选结果）")
        all_btn.clicked.connect(lambda: self._check_visible(True))
        none_btn = QPushButton("全不选")
        none_btn.clicked.connect(lambda: self._check_visible(False, everything=True))
        self.persons = QCheckBox("同时获取制作公司、监督、系列构成（慢一倍）")
        self.persons.setChecked(True)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop)
        self.add_btn = QPushButton("添加选中的作品")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self.add_checked)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        for w in (all_btn, none_btn, self.persons):
            bl.addWidget(w)
        bl.addStretch()
        for w in (self.progress, self.stop_btn, self.add_btn, close_btn):
            bl.addWidget(w)
        v.addLayout(bl)

        self._update_tag()
        self._update_add_btn()
        self.fetch()

    # ---------- 季度 ----------
    def _fill_seasons(self, select):
        self.season_box.blockSignals(True)
        self.season_box.clear()
        choices = season_choices(past=24, future=3)
        if select not in [s for s, _ in choices]:
            choices.append((select, season_label(select)))
        for s, label in choices:
            self.season_box.addItem(label, s)
            tag = season_tag(s)
            if tag:
                self.season_box.setItemData(self.season_box.count() - 1,
                                            CURRENT_COLOR if tag == "当季" else FUTURE_COLOR, Qt.ForegroundRole)
        self.season_box.setCurrentIndex(max(0, self.season_box.findData(select)))
        self.season_box.blockSignals(False)

    def season(self):
        return self.season_box.currentData()

    def _step(self, n):
        target = shift_season(self.season(), n)
        i = self.season_box.findData(target)
        if i < 0:
            self._fill_seasons(target)
            self.fetch()
        else:
            self.season_box.setCurrentIndex(i)

    def _update_tag(self):
        tag = season_tag(self.season())
        if tag == "当季":
            self.tag.setText("● 当季")
            self.tag.setStyleSheet(f"color: {CURRENT_COLOR.name()}; font-weight: bold;")
        elif tag == "未开播":
            self.tag.setText("未开播（已定档）")
            self.tag.setStyleSheet(f"color: {FUTURE_COLOR.name()}; font-weight: bold;")
        else:
            self.tag.setText("")
        sea = self.season()
        self.add_btn.setToolTip(f"加进「{season_label(sea)}」的季度表（按各自的开播日期归季）")

    # ---------- 拉取 ----------
    def fetch(self, force=False):
        sea = self.season()
        self._update_tag()
        if not sea:
            return
        if self.task is not None:
            self.task.cancelled = True
        self._fetch_gen += 1
        gen = self._fetch_gen
        if not force and sea in self.cache:
            self._fill(self.cache[sea])
            return
        self.table.setRowCount(0)
        self.info.setText(f"正在从 Bangumi 获取 {season_label(sea)} 开播的动画…")
        self._busy(True)
        client = self.client

        def job(report, cancelled):
            return client.browse_season(sea, progress=lambda a, b: report(a, b), cancelled=cancelled)

        self.task = run_async(job,
                              lambda items: self._fetched(gen, sea, items),
                              lambda err: self._fetch_failed(gen, err),
                              lambda i, n, _p: gen == self._fetch_gen and self._progress(i, n))

    def _progress(self, i, n):
        self.progress.setRange(0, max(n, 1))
        self.progress.setValue(i)
        self.info.setText(f"正在获取… {i} / {n}")

    def _fetched(self, gen, sea, items):
        if gen != self._fetch_gen:
            return
        self._busy(False)
        self.task = None
        self.cache[sea] = items
        log.info("季度新番 %s：%d 部", sea, len(items))
        self._fill(items)

    def _fetch_failed(self, gen, err):
        if gen != self._fetch_gen:
            return
        self._busy(False)
        self.task = None
        lines = (err or "").strip().splitlines()
        self.info.setText("获取失败：" + (lines[-1] if lines else "未知错误") + "。请检查网络后点「重新获取」")

    def _busy(self, on):
        self.progress.setVisible(on)
        self.stop_btn.setVisible(on)
        if on:
            self.progress.setRange(0, 0)
        self.reload_btn.setEnabled(not on)
        self.season_box.setEnabled(not on)
        self.prev_btn.setEnabled(not on)
        self.next_btn.setEnabled(not on)
        self._update_add_btn()

    def _stop(self):
        if self.task is not None:
            self.task.cancelled = True
        self.info.setText("正在停止…")

    # ---------- 列表 ----------
    def _have(self):
        """表里已有的：Bangumi 条目 id 集合、(作品名, 季度) 集合。"""
        ids, names = set(), set()
        for r in self.db.all():
            if r.get("bgm_id"):
                ids.add(r["bgm_id"])
            names.add(((r.get("title") or "").strip(), r.get("season")))
        return ids, names

    def _fill(self, items):
        ids, names = self._have()
        t = self.table
        t.blockSignals(True)
        t.setSortingEnabled(False)
        t.setRowCount(0)
        t.setRowCount(len(items))
        for i, subj in enumerate(items):
            sea = season_from_date(subj.get("date"))
            have = subj.get("id") in ids or ((subj.get("name_cn") or subj.get("name") or "").strip(), sea) in names
            chk = _SortItem()
            chk.setFlags(Qt.ItemIsEnabled | (Qt.NoItemFlags if have else Qt.ItemIsUserCheckable))
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, subj)
            chk.setData(Qt.UserRole + 1, 0)
            t.setItem(i, C_CHECK, chk)
            name = subj.get("name_cn") or subj.get("name") or ""
            it = _SortItem(name)
            it.setToolTip(f"{name}\n{subj.get('name') or ''}")
            t.setItem(i, C_NAME, it)
            t.setItem(i, C_DATE, _SortItem(subj.get("date") or ""))
            t.setItem(i, C_PLAT, _SortItem(platform_of(subj)))
            t.setItem(i, C_FMT, _SortItem(guess_format(subj)))
            eps = subj.get("total_episodes") or subj.get("eps") or 0
            e = _SortItem(str(eps) if eps else "")
            e.setData(Qt.UserRole + 1, int(eps or 0))
            t.setItem(i, C_EPS, e)
            tags = [x for x in subject_tag_names(subj) if x not in ("TV", "WEB", "日本", "剧场版", "OVA")]
            tg = _SortItem(" / ".join(tags[:6]))
            tg.setToolTip(" / ".join(tags))
            t.setItem(i, C_TAGS, tg)
            score = (subj.get("rating") or {}).get("score") or 0
            sc = _SortItem(f"{score:.1f}" if score else "")
            sc.setData(Qt.UserRole + 1, float(score or 0))
            t.setItem(i, C_SCORE, sc)
            hv = _SortItem("已在表里" if have else "")
            hv.setData(Qt.UserRole + 2, have)
            if have:
                hv.setForeground(QColor("#16A34A"))
                for c in (C_NAME, C_DATE):
                    t.item(i, c).setForeground(QColor("#9CA3AF"))
            t.setItem(i, C_HAVE, hv)
        if t.horizontalHeader().sortIndicatorSection() == C_CHECK:
            t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)   # 默认按 Bangumi 热度的顺序
        t.setSortingEnabled(True)
        t.blockSignals(False)
        self.apply_filter()

    def _subject(self, row):
        it = self.table.item(row, C_CHECK)
        return it.data(Qt.UserRole) if it else None

    def _row_have(self, row):
        it = self.table.item(row, C_HAVE)
        return bool(it and it.data(Qt.UserRole + 2))

    def matches(self, subj, have):
        if not self.plat[platform_of(subj)].isChecked():
            return False
        region = self.region.currentText()
        if region != REGIONS[0] and subject_region(subj) != region:
            return False
        fmt = self.fmt.currentData()
        if fmt and guess_format(subj) != fmt:
            return False
        if self.hide_adult.isChecked() and is_adult(subj):
            return False
        if self.hide_have.isChecked() and have:
            return False
        words = self.tags.text().strip().lower().split()
        if words:
            hay = " ".join(subject_tag_names(subj)).lower()
            if not all(w in hay for w in words):
                return False
        kw = self.kw.text().strip().lower()
        if kw and kw not in f"{subj.get('name_cn') or ''} {subj.get('name') or ''}".lower():
            return False
        return True

    def apply_filter(self):
        total = self.table.rowCount()
        shown = 0
        for r in range(total):
            subj = self._subject(r) or {}
            ok = self.matches(subj, self._row_have(r))
            self.table.setRowHidden(r, not ok)
            shown += ok
        sea = self.season()
        if total or sea in self.cache:
            self.info.setText(f"{season_label(sea)}：Bangumi 上共 {total} 部，筛选后 {shown} 部")
        self._update_add_btn()

    def _toggle_row(self, row, _col):
        it = self.table.item(row, C_CHECK)
        if it and it.flags() & Qt.ItemIsUserCheckable:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _check_visible(self, on, everything=False):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, C_CHECK)
            if it is None or not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            if everything or not self.table.isRowHidden(r):
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_add_btn()

    def _on_item_changed(self, item):
        if item.column() == C_CHECK:
            self._update_add_btn()

    def checked_subjects(self):
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, C_CHECK)
            if it is not None and it.checkState() == Qt.Checked and not self.table.isRowHidden(r):
                out.append(it.data(Qt.UserRole))
        return out

    def _update_add_btn(self):
        n = len(self.checked_subjects()) if hasattr(self, "table") else 0
        busy = self.stop_btn.isVisible() if hasattr(self, "stop_btn") else False
        self.add_btn.setText(f"添加选中的 {n} 部" if n else "添加选中的作品")
        self.add_btn.setEnabled(n > 0 and not busy)

    # ---------- 添加 ----------
    def add_checked(self):
        subs = self.checked_subjects()
        if not subs:
            return
        client, known, persons = self.client, dict(self.known), self.persons.isChecked()

        def job(report, cancelled):
            out = []
            for i, subj in enumerate(subs, 1):
                if cancelled():
                    break
                try:
                    fields = client.details(subj["id"], known, with_persons=persons)
                    rj = from_aliases(fields.get("bgm_name"), fields.get("aliases"))
                    fields["romaji"] = rj
                    fields["romaji_source"] = SOURCE_BANGUMI if rj else None
                    out.append((subj, fields, None))
                except Exception as e:  # noqa: BLE001  一部失败不影响其他
                    out.append((subj, None, str(e)))
                report(i, len(subs), None)
            return out

        self._busy(True)
        self.progress.setRange(0, len(subs))
        self.info.setText(f"正在获取 {len(subs)} 部作品的详情…")
        self.task = run_async(job, self._add_done, self._add_failed,
                              lambda i, n, _p: (self.progress.setValue(i),
                                                self.info.setText(f"正在获取详情… {i} / {n}")))

    def _add_failed(self, err):
        self._busy(False)
        self.task = None
        QMessageBox.warning(self, "添加失败", (err or "").strip().splitlines()[-1] if err else "未知错误")

    def _add_done(self, results):
        self._busy(False)
        self.task = None
        ids, names = self._have()
        added, skipped, failed = [], [], []
        target = self.season()
        self.db.backup("before_season_add")
        for subj, fields, err in results:
            name = subj.get("name_cn") or subj.get("name")
            if fields is None:
                failed.append(f"{name}：{err}")
                continue
            data = {k: v for k, v in fields.items() if v is not None}
            data["is_backlog"] = 0
            data.setdefault("season", season_from_date(subj.get("date")) or target)
            if fields.get("romaji"):
                data["extra"] = with_romaji_source({}, fields.get("romaji_source"))
            if data.get("bgm_id") in ids or ((data.get("title") or "").strip(), data.get("season")) in names:
                skipped.append(name)
                continue
            try:
                rid = self.db.add(data)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{name}：{e}")
                continue
            ids.add(data.get("bgm_id"))
            names.add(((data.get("title") or "").strip(), data.get("season")))
            added.append(rid)
            self.added_season = data.get("season")
        self.added_ids += added
        log.info("季度新番：添加 %d 部，跳过 %d 部，失败 %d 部", len(added), len(skipped), len(failed))
        msg = f"已添加 {len(added)} 部到季度表。"
        if skipped:
            msg += f"\n\n已经在表里、跳过 {len(skipped)} 部：" + "、".join(skipped[:10]) + \
                   ("…" if len(skipped) > 10 else "")
        if failed:
            msg += f"\n\n获取失败 {len(failed)} 部（可以稍后再试）：\n" + "\n".join(failed[:10])
        no_romaji = sum(1 for _s, f, _e in results if f is not None and not f.get("romaji"))
        if no_romaji and added:
            msg += f"\n\n其中 {no_romaji} 部在 Bangumi 别名里没找到罗马音，可以点工具栏的「补全罗马音」。"
        QMessageBox.information(self, "季度新番", msg)
        self._fill(self.cache.get(self.season(), []))

    def reject(self):
        if self.task is not None:
            self.task.cancelled = True
        super().reject()
