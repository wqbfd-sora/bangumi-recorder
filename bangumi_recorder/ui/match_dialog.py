"""批量关联 Bangumi：先自动搜索，再让你确认，最后补全信息。"""
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..bangumi import best_match, merge_fields, rank_candidates
from ..db import season_label
from .workers import run_async

SEARCH_LIMIT = 20          # 8 个不够：Bangumi 有时把要找的排在第 9 个之后（如 Re:0 第四季）

NONE_TEXT = "（不关联）"


def candidate_text(subj):
    name = subj.get("name_cn") or subj.get("name") or "?"
    return f"{name}（{subj.get('date') or '日期未知'}）#{subj.get('id')}"


class MatchDialog(QDialog):
    def __init__(self, db, client, rows, parent=None, known_studios=None):
        super().__init__(parent)
        self.known_studios = known_studios or {}
        self.db = db
        self.client = client
        self.rows = rows
        self.task = None
        self.changed = False
        self.setWindowTitle("批量关联 Bangumi")
        self.resize(900, 620)

        v = QVBoxLayout(self)
        self.info = QLabel(f"有 {len(rows)} 部作品还没有关联 Bangumi 条目。先「自动搜索」，"
                           "名称和季度都对得上的会自动选好（绿色），其余的（黄色）请在下拉框里手动选，"
                           "或保持「不关联」。\n搜不到或结果不对时，可以直接改左边的名称（比如换成官方译名、"
                           "日文原名），按回车只重新搜这一行。改的只是搜索词，不会改动你记录里的作品名。")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["我的记录（可改搜索词，回车重搜）", "季度", "Bangumi 条目", "状态"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.combos, self.edits = [], []
        for i, r in enumerate(rows):
            ed = QLineEdit(r["title"])
            ed.setToolTip(f"你的记录：{r['title']}\n这里改的只是搜索词，按回车重新搜索这一行")
            ed.returnPressed.connect(lambda i=i: self.search_row(i))
            ed.textEdited.connect(lambda _t, i=i: self._mark_edited(i))
            self.table.setCellWidget(i, 0, ed)
            self.edits.append(ed)
            self.table.setItem(i, 1, QTableWidgetItem(season_label(r["season"]) if r.get("season") else ""))
            cb = QComboBox()
            cb.addItem(NONE_TEXT, None)
            self.table.setCellWidget(i, 2, cb)
            self.combos.append(cb)
            self.table.setItem(i, 3, QTableWidgetItem("待搜索"))
        v.addWidget(self.table, 1)

        self.fill_blank = QCheckBox("只填写空白字段（推荐；不会改动你已经填过的内容）")
        self.fill_blank.setChecked(True)
        v.addWidget(self.fill_blank)
        note = QLabel("无论哪种方式，评分、状态、个人评价、作品名都不会被修改。")
        note.setObjectName("subtitle")
        v.addWidget(note)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        bl = QHBoxLayout()
        self.btn_search = QPushButton("自动搜索")
        self.btn_search.setObjectName("primary")
        self.btn_apply = QPushButton("应用所选")
        self.btn_apply.setEnabled(False)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_close = QPushButton("关闭")
        bl.addWidget(self.btn_search)
        bl.addWidget(self.btn_apply)
        bl.addWidget(self.btn_stop)
        bl.addStretch()
        bl.addWidget(self.btn_close)
        v.addLayout(bl)
        self.btn_search.clicked.connect(self.start_search)
        self.btn_apply.clicked.connect(self.start_apply)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_close.clicked.connect(self.close)

    def _busy(self, busy, total=0):
        self.btn_search.setEnabled(not busy)
        self.btn_apply.setEnabled(not busy and any(cb.count() > 1 for cb in self.combos))
        self.btn_stop.setEnabled(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, total)
        self.progress.setValue(0)

    def _set_status(self, i, text, color=None):
        self.table.item(i, 3).setText(text)
        if color:
            for c in (1, 3):
                self.table.item(i, c).setBackground(QBrush(QColor(color)))
            self.edits[i].setStyleSheet(f"QLineEdit {{ background: {color}; }}")

    def keyword(self, i):
        return self.edits[i].text().strip() or self.rows[i]["title"]

    def _mark_edited(self, i):
        changed = self.keyword(i) != self.rows[i]["title"]
        self._set_status(i, "已改搜索词，按回车搜索" if changed else "待搜索")

    # ---------- 第一步：搜索 ----------
    def start_search(self):
        keywords = [self.keyword(i) for i in range(len(self.rows))]
        client = self.client

        def job(report, cancelled):
            for i, kw in enumerate(keywords):
                if cancelled():
                    break
                try:
                    res = client.search(kw, limit=SEARCH_LIMIT)
                    report(i + 1, len(keywords), (i, res, None))
                except Exception as e:  # noqa: BLE001
                    report(i + 1, len(keywords), (i, [], str(e)))

        self._busy(True, len(keywords))
        self.task = run_async(job, lambda _r: self._busy(False), self._failed, self._on_search)

    def search_row(self, i):
        """只重新搜索一行（用左边改过的搜索词）。"""
        kw = self.keyword(i)
        self._set_status(i, f"正在搜索「{kw}」…")
        self.combos[i].setEnabled(False)
        client = self.client
        run_async(lambda: client.search(kw, limit=SEARCH_LIMIT),
                  lambda res: self._row_done(i, res, None),
                  lambda err: self._row_done(i, [], err))

    def _row_done(self, i, results, err):
        self.combos[i].setEnabled(True)
        self._fill_row(i, results, err)
        self.btn_apply.setEnabled(not self.btn_stop.isEnabled() and
                                  any(cb.count() > 1 for cb in self.combos))

    def _on_search(self, done, total, payload):
        self.progress.setValue(done)
        i, results, err = payload
        self._fill_row(i, results, err)

    def _fill_row(self, i, results, err):
        cb = self.combos[i]
        cb.clear()
        cb.addItem(NONE_TEXT, None)
        if err:
            self._set_status(i, "搜索失败", "#FDECEA")
            return
        results = rank_candidates(dict(self.rows[i], title=self.keyword(i)), results)
        for subj in results:
            cb.addItem(candidate_text(subj), subj)
        best = best_match(dict(self.rows[i], title=self.keyword(i)), results) \
            or best_match(self.rows[i], results)
        if best is not None:
            cb.setCurrentIndex(results.index(best) + 1)
            self._set_status(i, "已自动选好", "#E8F5E9")
        elif results:
            self._set_status(i, f"{len(results)} 个候选，请选择", "#FFF8E1")
        else:
            self._set_status(i, "没有结果", "#F3F4F6")

    # ---------- 第二步：应用 ----------
    def start_apply(self):
        todo = [(self.rows[i], cb.currentData()) for i, cb in enumerate(self.combos)
                if cb.currentData() is not None]
        if not todo:
            QMessageBox.information(self, "批量关联", "没有选中任何条目。")
            return
        self.db.backup("before_match")
        fill_blank = self.fill_blank.isChecked()
        client = self.client
        known = self.known_studios
        index_of = {id(r): i for i, r in enumerate(self.rows)}

        def job(report, cancelled):
            for n, (row, subj) in enumerate(todo, 1):
                if cancelled():
                    break
                try:
                    fields = client.details(subj["id"], known)
                    report(n, len(todo), (index_of[id(row)], merge_fields(row, fields, fill_blank), None))
                except Exception as e:  # noqa: BLE001
                    report(n, len(todo), (index_of[id(row)], None, str(e)))

        self._busy(True, len(todo))
        self.task = run_async(job, self._apply_done, self._failed, self._on_apply)

    def _on_apply(self, done, total, payload):
        self.progress.setValue(done)
        i, update, err = payload
        if err:
            self._set_status(i, "补全失败", "#FDECEA")
            return
        self.db.update(self.rows[i]["id"], update)
        self.rows[i].update(update)
        self.changed = True
        self._set_status(i, "已关联 ✓", "#C8E6C9")
        self.combos[i].setEnabled(False)
        self.edits[i].setEnabled(False)

    def _apply_done(self, _r):
        self._busy(False)
        self.info.setText("完成。已关联的作品可以在主界面看到 Bangumi 信息和封面。")

    def _failed(self, err):
        self._busy(False)
        QMessageBox.warning(self, "出错了", err[-800:])

    def stop(self):
        if self.task:
            self.task.cancelled = True

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)
