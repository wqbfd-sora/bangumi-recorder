"""一键刷新：把已关联作品的数据重新从 Bangumi 拉一遍。"""
import logging
from collections import defaultdict

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
                               QVBoxLayout)

from ..bangumi import merge_fields
from ..db import FIELD_LABELS, season_label
from .workers import run_async

log = logging.getLogger("bangumi_recorder.ui")
STAFF = ("studio", "director", "series_comp")


def duplicate_links(rows):
    """多部作品关联到同一个 Bangumi 条目的情况（通常是关联错了）。"""
    by_bid = defaultdict(list)
    for r in rows:
        if r.get("bgm_id"):
            by_bid[r["bgm_id"]].append(r)
    return {bid: rs for bid, rs in by_bid.items() if len(rs) > 1}


class RefreshDialog(QDialog):
    def __init__(self, db, client, all_rows, view_rows, known_studios, view_name, parent=None):
        super().__init__(parent)
        self.db = db
        self.client = client
        self.known = known_studios
        self.all_linked = [r for r in all_rows if r.get("bgm_id")]
        self.view_linked = [r for r in view_rows if r.get("bgm_id")]
        self.task = None
        self.changed_ids = set()
        self.report = defaultdict(list)
        self.setWindowTitle("刷新 Bangumi 数据")
        self.resize(640, 560)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("重新从 Bangumi 获取已关联作品的信息。"))

        v.addWidget(self._section("范围"))
        self.rb_all = QRadioButton(f"全部已关联的作品（{len(self.all_linked)} 部）")
        self.rb_view = QRadioButton(f"当前页面「{view_name}」里的（{len(self.view_linked)} 部）")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_all)
        grp.addButton(self.rb_view)
        self.rb_all.setChecked(True)
        self.rb_view.setEnabled(bool(self.view_linked))
        v.addWidget(self.rb_all)
        v.addWidget(self.rb_view)

        v.addWidget(self._section("更新哪些内容"))
        always = QLabel("· Bangumi 评分、排名、封面、简介、日文原名：总是更新")
        always.setObjectName("subtitle")
        v.addWidget(always)
        self.cb_broadcast = QCheckBox("播出季度、开播时间以 Bangumi 为准")
        self.cb_broadcast.setChecked(True)
        v.addWidget(self.cb_broadcast)
        self.cb_all = QCheckBox("集数、星期、体裁、制作公司、监督、系列构成、题材也以 Bangumi 为准"
                                "（不勾：只填空白的）")
        v.addWidget(self.cb_all)
        safe = QLabel("评分、状态、个人评价、作品名、罗马音不会被改动。开始前会自动备份数据库。")
        safe.setObjectName("subtitle")
        safe.setWordWrap(True)
        v.addWidget(safe)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("刷新结果会显示在这里")
        v.addWidget(self.log_box, 1)

        bl = QHBoxLayout()
        self.btn_start = QPushButton("开始刷新")
        self.btn_start.setObjectName("primary")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_close = QPushButton("关闭")
        bl.addWidget(self.btn_start)
        bl.addWidget(self.btn_stop)
        bl.addStretch()
        bl.addWidget(self.btn_close)
        v.addLayout(bl)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_close.clicked.connect(self.close)

    @staticmethod
    def _section(text):
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    def _busy(self, busy):
        for w in (self.btn_start, self.rb_all, self.rb_view, self.cb_broadcast, self.cb_all):
            w.setEnabled(not busy)
        if not busy:
            self.rb_view.setEnabled(bool(self.view_linked))
        self.btn_stop.setEnabled(busy)

    def start(self):
        rows = self.all_linked if self.rb_all.isChecked() else self.view_linked
        if not rows:
            return
        sync = self.cb_broadcast.isChecked()
        overwrite = self.cb_all.isChecked()
        backup = self.db.backup("before_refresh")
        log.info("刷新 Bangumi 数据：%d 部，季度同步=%s，全部覆盖=%s，备份=%s", len(rows), sync, overwrite, backup)
        self.report.clear()
        self.changed_ids.clear()
        self.log_box.setPlainText(f"已备份到 {backup.name}\n开始刷新 {len(rows)} 部作品……")
        self.progress.setRange(0, len(rows))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._busy(True)
        client, known = self.client, self.known

        def job(report, cancelled):
            for i, r in enumerate(rows, 1):
                if cancelled():
                    break
                need_staff = overwrite or any(not r.get(k) for k in STAFF)
                try:
                    fields = client.details(r["bgm_id"], known, with_persons=need_staff)
                    update = merge_fields(r, fields, fill_only_blank=not overwrite, sync_broadcast=sync)
                    report(i, len(rows), (r, update, None))
                except Exception as e:  # noqa: BLE001
                    report(i, len(rows), (r, None, str(e).strip().splitlines()[-1] if str(e) else "?"))

        self.task = run_async(job, self._done, self._failed, self._on_item)

    def _on_item(self, i, n, payload):
        self.progress.setValue(i)
        r, update, err = payload
        if err:
            self.report["失败"].append(f"{r['title']}：{err}")
            return
        if not update:
            return
        self.db.update(r["id"], update)
        self.changed_ids.add(r["id"])
        if "season" in update:
            old = season_label(r["season"]) if r.get("season") else "（空）"
            self.report["播出季度有变化"].append(f"{r['title']}：{old} → {season_label(update['season'])}")
        others = [FIELD_LABELS.get(k, k) for k in update
                  if k not in ("season", "air_date", "extra") and not k.startswith("bgm_")
                  and k not in ("cover_url", "summary")]
        if others:
            self.report["其他字段有更新"].append(f"{r['title']}：{'、'.join(others)}")

    def _done(self, _res):
        self._busy(False)
        rows = self.db.all()
        lines = [f"完成。更新了 {len(self.changed_ids)} 部作品。"]
        if self.task and self.task.cancelled:
            lines[0] = f"已停止。之前的 {len(self.changed_ids)} 部已经更新。"
        for title in ("播出季度有变化", "其他字段有更新", "失败"):
            items = self.report.get(title)
            if items:
                lines.append(f"\n【{title}】（{len(items)}）")
                lines.extend(f"  · {x}" for x in items)
        dups = duplicate_links(rows)
        if dups:
            lines.append(f"\n【这些作品关联到了同一个 Bangumi 条目，可能有关联错的】（{len(dups)} 组）")
            for bid, rs in dups.items():
                lines.append(f"  · #{bid}：" + "、".join(r["title"] for r in rs))
        self.log_box.setPlainText("\n".join(lines))
        log.info("刷新完成：%d 部有更新", len(self.changed_ids))

    def _failed(self, err):
        self._busy(False)
        self.log_box.appendPlainText("\n出错了：" + err.strip().splitlines()[-1])

    def stop(self):
        if self.task:
            self.task.cancelled = True

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)
