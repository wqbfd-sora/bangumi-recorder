"""评分计算器对话框。"""
import json
from decimal import Decimal

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from ..db import fmt_score, score_tier
from ..scoring import DEFAULT_ITEMS, quick_score, ratio_text, weighted_score
from .style import score_color

SETTING_KEY = "score_calc_items"


def load_items(db):
    try:
        items = json.loads(db.get_setting(SETTING_KEY) or "null")
        if isinstance(items, list) and items and all("name" in i and "weight" in i for i in items):
            return items
    except (ValueError, TypeError):
        pass
    return [dict(i) for i in DEFAULT_ITEMS]


class ScoreSpinBox(QDoubleSpinBox):
    """分数输入框：直接打 86 就是 8.6（也可以打 8.6）；输完一个分数会发 completed。
    0 显示为「未评」，不参与计算。获得焦点时全选，方便直接改写。"""
    completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 10)
        self.setDecimals(1)
        self.setSingleStep(0.5)
        self.setSpecialValueText("未评")
        self.setToolTip("直接输入两位数字：86 = 8.6，9 = 9.0，10 = 满分；也可以输 8.6")
        self._last_len = 0
        self.lineEdit().textEdited.connect(self._edited)

    def validate(self, text, pos):
        special = self.specialValueText()
        if text == special:
            return QValidator.Acceptable, text, pos
        if text and special.startswith(text):          # 「未评」删掉一个字时允许
            return QValidator.Intermediate, text, pos
        state, _, _ = quick_score(text)
        return ({"ok": QValidator.Acceptable, "partial": QValidator.Intermediate}
                .get(state, QValidator.Invalid), text, pos)

    def valueFromText(self, text):
        if text and self.specialValueText().startswith(text):
            return self.minimum()
        _, val, _ = quick_score(text)
        return val if val is not None else self.value()

    def textFromValue(self, value):
        return f"{value:.1f}"

    def fixup(self, text):
        _, val, _ = quick_score(text)
        return self.textFromValue(val if val is not None else self.value())

    def _edited(self, text):
        grew = len(text) > self._last_len              # 只有往里打字时才跳，删字不跳
        self._last_len = len(text)
        if grew and quick_score(text)[2]:
            QTimer.singleShot(0, self, self.completed.emit)

    def keyPressEvent(self, e):
        le = self.lineEdit()
        self._last_len = len(le.text()) - len(le.selectedText())   # 这次按键之前剩下的长度
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        QTimer.singleShot(0, self, self.selectAll)


class ScoreCalculator(QDialog):
    """scores：这部作品上次保存的分项（可为空）。确定后 result（Decimal）和 scores 可读。"""

    def __init__(self, db, title, scores=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.items = load_items(db)
        self.initial = dict(scores or {})
        self.result = None
        self.scores = {}
        self.setWindowTitle("评分计算器")
        self.setMinimumWidth(420)

        v = QVBoxLayout(self)
        head = QLabel(title)
        head.setObjectName("title")
        head.setWordWrap(True)
        v.addWidget(head)
        self.formula = QLabel()
        self.formula.setObjectName("subtitle")
        v.addWidget(self.formula)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 6, 0, 6)
        self.grid.setHorizontalSpacing(8)
        for j, text in enumerate(("项目", "分数（86 = 8.6）", "比例", "")):
            lab = QLabel(text)
            lab.setObjectName("subtitle")
            self.grid.addWidget(lab, 0, j)
        v.addWidget(self.grid_host)

        tools = QHBoxLayout()
        self.btn_add = QPushButton("＋ 添加一项")
        self.btn_add.clicked.connect(self._add_item)
        self.btn_reset = QPushButton("恢复默认（剧情 5 : 制作 3 : 节奏 2）")
        self.btn_reset.clicked.connect(self._reset)
        tools.addWidget(self.btn_add)
        tools.addWidget(self.btn_reset)
        tools.addStretch()
        v.addLayout(tools)

        res = QHBoxLayout()
        self.big = QLabel("—")
        self.big.setObjectName("bigScore")
        self.tier = QLabel()
        self.tier.setObjectName("tier")
        self.raw = QLabel()
        self.raw.setObjectName("subtitle")
        col = QVBoxLayout()
        col.addWidget(self.tier, 0, Qt.AlignLeft)
        col.addWidget(self.raw)
        res.addWidget(self.big)
        res.addLayout(col, 1)
        v.addLayout(res)

        note = QLabel("分数直接打数字：86 → 8.6，9 → 9.0，10 → 满分，输完自动跳到下一项，最后按回车填入。"
                      "结果向上取整到小数点后一位（如 8.33 → 8.4）。名称和比例改动后会记住，下次默认使用。")
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        v.addWidget(note)

        bl = QHBoxLayout()
        bl.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("填入终评")
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        for b in (cancel, self.btn_add, self.btn_reset):
            b.setAutoDefault(False)              # 回车只触发「填入终评」
        self.btn_ok.clicked.connect(self._accept)
        bl.addWidget(cancel)
        bl.addWidget(self.btn_ok)
        v.addLayout(bl)

        self.rows = []
        self._build_rows()

    # ---------- 行 ----------
    def _build_rows(self):
        # 记住当前已填的分数，重建后放回去
        current = {r["name"].text().strip(): r["score"].value() for r in self.rows
                   if r["name"].text().strip()}
        for r in self.rows:
            for w in r.values():
                w.deleteLater()
        self.rows = []
        for i, it in enumerate(self.items, start=1):
            name = QLineEdit(it["name"])
            name.setMaximumWidth(110)
            score = ScoreSpinBox()
            prev = current.get(it["name"], self.initial.get(it["name"]))
            score.setValue(float(prev) if prev is not None else 0)
            weight = QDoubleSpinBox()
            weight.setRange(0, 100)
            weight.setDecimals(1)
            weight.setSingleStep(1)
            weight.setValue(float(it["weight"]))
            weight.setMaximumWidth(80)
            rm = QToolButton()
            rm.setText("✕")
            rm.setToolTip("删除这一项")
            rm.setEnabled(len(self.items) > 1)
            rm.clicked.connect(lambda _c=False, idx=i - 1: self._remove_item(idx))
            for w in (score, weight):
                w.valueChanged.connect(self._recalc)
            name.textChanged.connect(self._recalc)
            self.grid.addWidget(name, i, 0)
            self.grid.addWidget(score, i, 1)
            self.grid.addWidget(weight, i, 2)
            self.grid.addWidget(rm, i, 3)
            self.rows.append({"name": name, "score": score, "weight": weight, "rm": rm})
        for a, b in zip(self.rows, self.rows[1:]):
            a["score"].completed.connect(lambda nxt=b["score"]: nxt.setFocus(Qt.TabFocusReason))
        self._recalc()
        if self.rows:
            self.rows[0]["score"].setFocus()
            self.rows[0]["score"].selectAll()

    def _collect_items(self):
        items = []
        for r in self.rows:
            name = r["name"].text().strip()
            if name:
                items.append({"name": name, "weight": r["weight"].value()})
        return items

    def _add_item(self):
        items = self._collect_items()
        used = {i["name"] for i in items}
        n = len(items) + 1
        while f"项目{n}" in used:
            n += 1
        self.items = items + [{"name": f"项目{n}", "weight": 1}]
        self._build_rows()

    def _remove_item(self, idx):
        items = self._collect_items()
        if 0 <= idx < len(items) and len(items) > 1:
            items.pop(idx)
        self.items = items
        self._build_rows()

    def _reset(self):
        self.items = [dict(i) for i in DEFAULT_ITEMS]
        self._build_rows()

    # ---------- 计算 ----------
    def _scores(self):
        out = {}
        for r in self.rows:
            name = r["name"].text().strip()
            if name and r["score"].value() > 0:
                out[name] = r["score"].value()
        return out

    def _recalc(self, *_):
        items = self._collect_items()
        self.formula.setText(" : ".join(i["name"] for i in items if i["weight"] > 0)
                             + " = " + ratio_text(items))
        names = [i["name"] for i in items]
        dup = sorted({n for n in names if names.count(n) > 1})
        raw, final = weighted_score(self._scores(), items)
        if dup:
            self.big.setText("—")
            self.big.setStyleSheet("color: #9CA3AF;")
            self.tier.setVisible(False)
            self.raw.setText(f"项目名称重复：{'、'.join(dup)}，请改成不同的名字")
            self.btn_ok.setEnabled(False)
            return
        if final is None:
            self.big.setText("—")
            self.big.setStyleSheet("color: #9CA3AF;")
            self.tier.setText("")
            self.tier.setVisible(False)
            self.raw.setText("填好各项分数后自动计算")
            self.btn_ok.setEnabled(False)
            return
        val = float(final)
        self.big.setText(fmt_score(val))
        self.big.setStyleSheet(f"color: {score_color(val).name()};")
        self.tier.setText(score_tier(val))
        self.tier.setVisible(True)
        missing = [i["name"] for i in items if i["weight"] > 0 and i["name"] not in self._scores()]
        txt = f"加权平均 {raw.quantize(Decimal('0.001'))} → 向上取整 {fmt_score(val)}"
        if missing:
            txt += f"\n（{'、'.join(missing)} 还没打分，暂不计入）"
        self.raw.setText(txt)
        self.btn_ok.setEnabled(True)

    def _accept(self):
        items = self._collect_items()
        names = [i["name"] for i in items]
        if len(set(names)) != len(names):
            return
        _, final = weighted_score(self._scores(), items)
        if final is None:
            return
        self.result = final
        self.scores = self._scores()
        self.db.set_setting(SETTING_KEY, json.dumps(items, ensure_ascii=False))
        self.accept()
