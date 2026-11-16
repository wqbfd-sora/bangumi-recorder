"""小对话框：评分标准与选项、设置、导入报告。"""
import os
import subprocess
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import appconfig, config, errors
from . import fonts
from .style import DEFAULT_PT


def _color_swatch(color):
    pm = QPixmap(36, 36)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(1, 1, 16, 16), 5, 5)
    p.end()
    return QIcon(pm)


class _EditTable(QTableWidget):
    """配置表格：能加行、删行、上下移动。"""

    def __init__(self, headers, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.horizontalHeader().setStretchLastSection(True)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.verticalHeader().setDefaultSectionSize(34)

    def move_current(self, step):
        r = self.currentRow()
        t = r + step
        if r < 0 or not 0 <= t < self.rowCount():
            return
        for c in range(self.columnCount()):
            a, b = self.takeItem(r, c), self.takeItem(t, c)
            self.setItem(r, c, b)
            self.setItem(t, c, a)
        self.setCurrentCell(t, self.currentColumn())


class OptionsDialog(QDialog):
    """评分标准（名作之壁）、观看状态、体裁。保存到 data/config.json，表格里的评级、颜色、
    下拉选项都跟着它走。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.cfg = db.cfg
        self.setWindowTitle("评分标准与选项")
        self.resize(860, 600)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self._build_rubric_tab()
        self._build_status_tab()
        self._build_format_tab()

        bl = QHBoxLayout()
        open_btn = QPushButton("打开配置文件")
        open_btn.setToolTip(f"{self.cfg.path}\n也可以直接用记事本改，改完重启程序或再打开这个窗口")
        open_btn.clicked.connect(lambda: open_folder(self.cfg.path) if self.cfg.path.exists()
                                 else open_folder(self.cfg.path.parent))
        reset_btn = QPushButton("恢复这一页的默认值")
        reset_btn.clicked.connect(self._reset_page)
        bl.addWidget(open_btn)
        bl.addWidget(reset_btn)
        bl.addStretch()
        bb = QDialogButtonBox()
        save = bb.addButton("保存", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        bl.addWidget(bb)
        v.addLayout(bl)

    # ---------- 评分标准 ----------
    def _build_rubric_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        tip = QLabel("「分数」的写法：<b>9</b>（正好 9 分）、<b>(8,9)</b>（大于 8 小于 9）、<b>[8,9)</b>、"
                     "<b>8-9</b>、<b>小于等于4</b>、<b>9以上</b>。从上往下找第一个对得上的档位。"
                     "点颜色格可以换颜色。")
        tip.setWordWrap(True)
        tip.setObjectName("subtitle")
        v.addWidget(tip)
        self.rt = _EditTable(["分数", "等级", "颜色", "代表作", "说明"])
        self.rt.setColumnWidth(0, 110)
        self.rt.setColumnWidth(1, 120)
        self.rt.setColumnWidth(2, 120)
        self.rt.setColumnWidth(3, 200)
        self.rt.cellDoubleClicked.connect(self._rubric_cell)
        self.rt.cellClicked.connect(lambda r, c: c == 2 and self._pick_color(self.rt, r, 2))
        self.rt.itemChanged.connect(self._check_rubric)
        self._fill_rubric(self.cfg.rubric())
        v.addWidget(self.rt, 1)
        self.r_warn = QLabel()
        self.r_warn.setStyleSheet("color: #C62828;")
        self.r_warn.setWordWrap(True)
        v.addWidget(self.r_warn)
        tl = QHBoxLayout()
        tl.addWidget(QLabel("试一下："))
        self.try_score = QLineEdit()
        self.try_score.setPlaceholderText("输入分数，如 8.5")
        self.try_score.setMaximumWidth(140)
        self.try_out = QLabel()
        self.try_score.textChanged.connect(self._try)
        tl.addWidget(self.try_score)
        tl.addWidget(self.try_out, 1)
        v.addLayout(tl)
        v.addLayout(self._row_buttons(self.rt, lambda: self._add_rubric_row()))
        self.tabs.addTab(page, "评分标准（名作之壁）")
        self._check_rubric()

    def _fill_rubric(self, rows):
        self.rt.blockSignals(True)
        self.rt.setRowCount(0)
        for r in rows:
            self._add_rubric_row(r)
        self.rt.blockSignals(False)

    def _add_rubric_row(self, r=None):
        r = r or {}
        i = self.rt.rowCount()
        self.rt.insertRow(i)
        for j, k in ((0, "score"), (1, "level"), (3, "example"), (4, "description")):
            self.rt.setItem(i, j, QTableWidgetItem(r.get(k) or ""))
        self._set_color_item(self.rt, i, 2, r.get("color") or "")

    def _rubric_cell(self, row, col):
        if col == 2:
            self._pick_color(self.rt, row, 2)

    def _rubric_rows(self):
        rows = []
        for i in range(self.rt.rowCount()):
            def t(j):
                it = self.rt.item(i, j)
                return it.text().strip() if it else ""
            color_it = self.rt.item(i, 2)
            row = {"score": t(0), "level": t(1), "example": t(3), "description": t(4),
                   "color": color_it.data(Qt.UserRole) if color_it else ""}
            if any(row[k] for k in ("score", "level", "example", "description")):
                rows.append(row)
        return rows

    def _check_rubric(self, *_):
        bad = []
        for i in range(self.rt.rowCount()):
            it = self.rt.item(i, 0)
            text = it.text().strip() if it else ""
            ok = not text or appconfig.parse_range(text) is not None
            if it is not None:
                self.rt.blockSignals(True)
                it.setForeground(QColor("#1F2328") if ok else QColor("#C62828"))
                self.rt.blockSignals(False)
            if not ok:
                bad.append(text)
        self.r_warn.setText(("看不懂这些分数写法（这些档位不会生效）：" + "、".join(bad)) if bad else "")
        self._try()

    def _try(self, *_):
        text = self.try_score.text().strip().replace("，", ".").replace(",", ".")
        if not text:
            self.try_out.setText("")
            return
        try:
            s = float(text)
        except ValueError:
            self.try_out.setText("不是数字")
            return
        tmp = appconfig.AppConfig.__new__(appconfig.AppConfig)
        tmp.data = appconfig.AppConfig._normalized({"rubric": self._rubric_rows()})
        tmp._compile()
        name, color = tmp.tier(s)
        self.try_out.setText(f'<b style="color:{color}">{name or "（没有对应的档位）"}</b>')

    # ---------- 观看状态 ----------
    def _build_status_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        tip = QLabel("表格里点住「状态」格就能从这些选项里拖选。改了名字的状态，表里用到它的作品会一起改名。"
                     "「海报角标」决定海报墙封面左下角显不显示这个状态。")
        tip.setWordWrap(True)
        tip.setObjectName("subtitle")
        v.addWidget(tip)
        self.st = _EditTable(["名称", "颜色", "海报角标"])
        self.st.setColumnWidth(0, 200)
        self.st.setColumnWidth(1, 130)
        self.st.cellClicked.connect(lambda r, c: c == 1 and self._pick_color(self.st, r, 1))
        for s in self.cfg.statuses():
            self._add_status_row(s)
        v.addWidget(self.st, 1)
        v.addLayout(self._row_buttons(self.st, lambda: self._add_status_row()))
        self.tabs.addTab(page, "观看状态")

    def _add_status_row(self, s=None):
        s = s or {"name": "", "color": appconfig.FALLBACK_COLOR, "badge": True}
        i = self.st.rowCount()
        self.st.insertRow(i)
        name = QTableWidgetItem(s["name"])
        name.setData(Qt.UserRole, s["name"] or None)       # 原来的名字：用来识别改名
        self.st.setItem(i, 0, name)
        self._set_color_item(self.st, i, 1, s["color"])
        badge = QTableWidgetItem("")
        badge.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        badge.setCheckState(Qt.Checked if s.get("badge", True) else Qt.Unchecked)
        self.st.setItem(i, 2, badge)

    def _status_rows(self):
        out = []
        for i in range(self.st.rowCount()):
            it = self.st.item(i, 0)
            name = it.text().strip() if it else ""
            if not name:
                continue
            out.append({"name": name, "orig": it.data(Qt.UserRole),
                        "color": self.st.item(i, 1).data(Qt.UserRole) or appconfig.FALLBACK_COLOR,
                        "badge": self.st.item(i, 2).checkState() == Qt.Checked})
        return out

    # ---------- 体裁 ----------
    def _build_format_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        tip = QLabel("体裁的下拉选项。改了名字的体裁，表里用到它的作品会一起改名。")
        tip.setWordWrap(True)
        tip.setObjectName("subtitle")
        v.addWidget(tip)
        self.ft = _EditTable(["名称"])
        for f in self.cfg.formats():
            self._add_format_row(f)
        v.addWidget(self.ft, 1)
        v.addLayout(self._row_buttons(self.ft, lambda: self._add_format_row()))
        self.tabs.addTab(page, "体裁")

    def _add_format_row(self, name=""):
        i = self.ft.rowCount()
        self.ft.insertRow(i)
        it = QTableWidgetItem(name)
        it.setData(Qt.UserRole, name or None)
        self.ft.setItem(i, 0, it)

    def _format_rows(self):
        out = []
        for i in range(self.ft.rowCount()):
            it = self.ft.item(i, 0)
            if it and it.text().strip():
                out.append({"name": it.text().strip(), "orig": it.data(Qt.UserRole)})
        return out

    # ---------- 公用 ----------
    def _row_buttons(self, table, add):
        bl = QHBoxLayout()
        for text, fn in (("添加一行", add),
                         ("删除选中行", lambda: table.currentRow() >= 0 and table.removeRow(table.currentRow())),
                         ("上移", lambda: table.move_current(-1)),
                         ("下移", lambda: table.move_current(1))):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch()
        return bl

    @staticmethod
    def _set_color_item(table, row, col, color):
        it = QTableWidgetItem(color or "自动")
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        it.setData(Qt.UserRole, color or "")
        it.setIcon(_color_swatch(color or "#E5E7EB"))
        it.setToolTip("点一下换颜色")
        table.setItem(row, col, it)

    def _pick_color(self, table, row, col):
        it = table.item(row, col)
        cur = QColor(it.data(Qt.UserRole) or "#E8707E") if it else QColor("#E8707E")
        c = QColorDialog.getColor(cur, self, "选择颜色")
        if c.isValid():
            self._set_color_item(table, row, col, c.name().upper())
            if table is self.rt:
                self._try()

    def _reset_page(self):
        i = self.tabs.currentIndex()
        if i == 0:
            self._fill_rubric([dict(r, example="", description="") for r in appconfig.DEFAULT_RUBRIC])
            self._check_rubric()
        elif i == 1:
            self.st.setRowCount(0)
            for s in appconfig.DEFAULT_STATUSES:
                self._add_status_row(s)
        else:
            self.ft.setRowCount(0)
            for f in appconfig.DEFAULT_FORMATS:
                self._add_format_row(f)

    def _renames(self, rows, column):
        """改了名字的选项：(旧名, 新名, 用到旧名的作品数)。"""
        out = []
        for r in rows:
            if r["orig"] and r["orig"] != r["name"]:
                n = self.db.conn.execute(f"SELECT COUNT(*) FROM anime WHERE {column} = ?",
                                         (r["orig"],)).fetchone()[0]
                if n:
                    out.append((r["orig"], r["name"], n))
        return out

    def _save(self):
        statuses, formats = self._status_rows(), self._format_rows()
        for rows, what in ((statuses, "状态"), (formats, "体裁")):
            names = [r["name"] for r in rows]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                QMessageBox.warning(self, "名字重复", f"{what}里有重复的名字：{'、'.join(dup)}")
                return
        renames = [("status", *x) for x in self._renames(statuses, "status")] + \
                  [("format", *x) for x in self._renames(formats, "format")]
        if renames:
            lines = "\n".join(f"{'状态' if c == 'status' else '体裁'}：{a} → {b}（{n} 部）" for c, a, b, n in renames)
            ans = QMessageBox.question(self, "一起改名？", f"这些选项改了名字：\n\n{lines}\n\n"
                                       "要把表里用到它们的作品也一起改过来吗？（会先自动备份）",
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.Yes:
                self.db.backup("before_rename_option")
                with self.db.conn:
                    for col, a, b, _n in renames:
                        self.db.conn.execute(f"UPDATE anime SET {col} = ? WHERE {col} = ?", (b, a))
        self.cfg.data["rubric"] = self._rubric_rows()
        self.cfg.data["statuses"] = [{k: r[k] for k in ("name", "color", "badge")} for r in statuses]
        self.cfg.data["formats"] = [r["name"] for r in formats]
        try:
            self.cfg.save()
        except OSError as e:
            QMessageBox.warning(self, "保存失败", f"配置文件写不进去：{e}")
            return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.restored = False
        self.setWindowTitle("设置")
        self.resize(720, 380)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.font_pt = QSpinBox()
        self.font_pt.setRange(9, 16)
        self.font_pt.setSuffix(" pt")
        self.font_pt.setValue(int(db.get_setting("font_pt") or DEFAULT_PT))
        form.addRow("界面字号", self.font_pt)
        self.font_key = QComboBox()
        for key, label, _fams in fonts.PRESETS:
            self.font_key.addItem(label, key)
        self.font_key.addItem("其他已安装的字体…", "custom")
        self.font_custom = QFontComboBox()
        cur = db.get_setting("font_family") or fonts.DEFAULT_KEY
        if cur.startswith("custom:"):
            self.font_key.setCurrentIndex(self.font_key.count() - 1)
            from PySide6.QtGui import QFont
            self.font_custom.setCurrentFont(QFont(cur[len("custom:"):]))
        else:
            i = self.font_key.findData(cur)
            self.font_key.setCurrentIndex(max(0, i))
        self.font_custom.setVisible(self.font_key.currentData() == "custom")
        self.font_key.currentIndexChanged.connect(
            lambda _i: self.font_custom.setVisible(self.font_key.currentData() == "custom"))
        fbox = QWidget()
        fl = QHBoxLayout(fbox)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.font_key, 1)
        fl.addWidget(self.font_custom, 1)
        form.addRow("界面字体", fbox)
        flic = QLabel("自带的思源黑体（Noto Sans SC / JP）和 Inter 都是开源免费、可商用的字体（SIL OFL 1.1）。")
        flic.setObjectName("subtitle")
        flic.setWordWrap(True)
        form.addRow("", flic)
        self.token = QLineEdit(db.get_setting("bgm_token") or "")
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("可不填。搜索和查看条目不需要登录")
        form.addRow("Bangumi Access Token", self.token)
        link = QLabel('<a href="https://next.bgm.tv/demo/access-token">在这里生成 Access Token</a>'
                      "（以后同步收藏、查看受限条目时才需要）")
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        form.addRow("", link)
        path = QLabel(str(config.DATA_DIR))
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("数据文件夹", path)
        v.addLayout(form)

        bl = QHBoxLayout()
        open_btn = QPushButton("打开数据文件夹")
        open_btn.clicked.connect(lambda: open_folder(config.DATA_DIR))
        backup_btn = QPushButton("立即备份数据库")
        backup_btn.clicked.connect(self._backup)
        log_btn = QPushButton("打开日志文件夹")
        log_btn.setToolTip("出错或闪退时的记录在这里（app.log / crash.log）")
        log_btn.clicked.connect(errors.open_log_folder)
        restore_btn = QPushButton("从备份恢复…")
        restore_btn.setToolTip("用 data/backups/ 里的某份备份替换当前数据（会先自动备份当前数据）")
        restore_btn.clicked.connect(self._restore)
        bl.addWidget(open_btn)
        bl.addWidget(backup_btn)
        bl.addWidget(restore_btn)
        bl.addWidget(log_btn)
        bl.addStretch()
        v.addLayout(bl)
        v.addStretch()

        bb = QDialogButtonBox()
        ok = bb.addButton("保存", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _restore(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择要恢复的备份", str(config.BACKUP_DIR),
                                              "数据库备份 (*.db)")
        if not path:
            return
        if QMessageBox.question(self, "从备份恢复",
                                f"用这份备份替换当前的全部数据？\n\n{path}\n\n"
                                "替换前会先把当前数据再备份一份，后悔了还能换回来。") != QMessageBox.Yes:
            return
        try:
            safety = self.db.restore_from(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "恢复失败", f"这个文件不能用来恢复：{e}")
            return
        self.restored = True
        QMessageBox.information(self, "恢复完成", f"已恢复。原来的数据备份在：\n{safety}")

    def _backup(self):
        p = self.db.backup("manual")
        QMessageBox.information(self, "备份完成", f"已备份到：\n{p}")

    def _save(self):
        self.db.set_setting("bgm_token", self.token.text().strip())
        self.db.set_setting("font_pt", str(self.font_pt.value()))
        self.db.set_setting("font_family", self.font_choice())
        self.accept()

    def font_choice(self):
        key = self.font_key.currentData()
        if key == "custom":
            return "custom:" + self.font_custom.currentFont().family()
        return key or fonts.DEFAULT_KEY


class TextDialog(QDialog):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 480)
        v = QVBoxLayout(self)
        t = QPlainTextEdit(text)
        t.setReadOnly(True)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


def open_folder(path):
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
