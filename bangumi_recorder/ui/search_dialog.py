"""Bangumi 搜索对话框：添加新作品，或给已有作品关联条目。"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton,
                               QVBoxLayout)

from .. import appconfig
from ..bangumi import guess_format, rank_candidates, subject_tag_names
from ..db import season_from_date, season_label
from .season_util import season_choices, season_tag
from .workers import run_async

THUMB = QSize(48, 64)


def describe(subj):
    name_cn = subj.get("name_cn") or ""
    name = subj.get("name") or ""
    first = name_cn or name
    meta = []
    if subj.get("date"):
        meta.append(subj["date"])
    if subj.get("platform"):
        meta.append(subj["platform"])
    eps = subj.get("total_episodes") or subj.get("eps")
    if eps:
        meta.append(f"{eps} 话")
    score = (subj.get("rating") or {}).get("score") or subj.get("score")
    if score:
        meta.append(f"★ {score:.1f}")
    second = name if name_cn and name != name_cn else ""
    return f"{first}\n{second + '  ' if second else ''}{' · '.join(meta)}"


class SearchDialog(QDialog):
    """mode='add'：添加作品（可选放进季度表或补番）；mode='link'：为已有作品关联条目。"""

    def __init__(self, client, keyword="", mode="add", default_backlog=False, parent=None, season=None):
        super().__init__(parent)
        self.client = client
        self.season = season             # 关联时：这部作品的季度，同季度的候选排在前面
        self.mode = mode
        self.selected = None
        self.manual = False
        self._thumb_tasks = []
        self._gen = 0                    # 每次搜索 +1；旧搜索的结果/缩略图回来时直接丢掉
        self.setWindowTitle("添加作品" if mode == "add" else "关联 Bangumi 条目")
        self.resize(680, 620)
        self.results = []

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.edit = QLineEdit(keyword)
        self.edit.setPlaceholderText("输入作品名，回车搜索 Bangumi（选了季度也可以不填）")
        self.edit.returnPressed.connect(self.search)
        btn = QPushButton("搜索")
        btn.setObjectName("primary")
        btn.clicked.connect(self.search)
        top.addWidget(self.edit, 1)
        top.addWidget(btn)
        v.addLayout(top)

        # 筛选：开播季度（在 Bangumi 上就按季度搜）、体裁、题材（在结果里筛）
        flt = QHBoxLayout()
        self.f_season = QComboBox()
        self.f_season.addItem("任意季度", None)
        for sea, label in season_choices(past=16, future=2):
            self.f_season.addItem(label, sea)
        self.f_season.setToolTip("选了季度以后，不填作品名直接搜索，就是列出这一季的动画（按热度）")
        self.f_season.currentIndexChanged.connect(self.search)
        self.f_format = QComboBox()
        self.f_format.addItem("任意体裁", None)
        for f in appconfig.current().formats():
            self.f_format.addItem(f, f)
        self.f_format.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self.f_tag = QLineEdit()
        self.f_tag.setPlaceholderText("题材，如 恋爱 / 漫画改 / MAPPA")
        self.f_tag.setClearButtonEnabled(True)
        self.f_tag.textChanged.connect(lambda _t: self._apply_filter())
        flt.addWidget(QLabel("筛选："))
        flt.addWidget(self.f_season)
        flt.addWidget(self.f_format)
        flt.addWidget(self.f_tag, 1)
        v.addLayout(flt)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        v.addWidget(self.status)

        self.list = QListWidget()
        self.list.setIconSize(THUMB)
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept_selected())
        self.list.currentItemChanged.connect(self._on_current)
        v.addWidget(self.list, 1)

        self.season_hint = QLabel("")
        self.season_hint.setObjectName("subtitle")
        v.addWidget(self.season_hint)

        if mode == "add":
            row = QHBoxLayout()
            row.addWidget(QLabel("加入："))
            self.rb_season = QRadioButton("季度表（按开播时间自动归季）")
            self.rb_backlog = QRadioButton("补番")
            grp = QButtonGroup(self)
            grp.addButton(self.rb_season)
            grp.addButton(self.rb_backlog)
            (self.rb_backlog if default_backlog else self.rb_season).setChecked(True)
            row.addWidget(self.rb_season)
            row.addWidget(self.rb_backlog)
            row.addStretch()
            v.addLayout(row)

        buttons = QDialogButtonBox()
        self.ok = buttons.addButton("添加" if mode == "add" else "关联", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        if mode == "add":
            manual = buttons.addButton("手动添加（不用 Bangumi）", QDialogButtonBox.ActionRole)
            manual.clicked.connect(self._manual)
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        if keyword:
            self.search()

    @property
    def backlog(self):
        return self.mode == "add" and self.rb_backlog.isChecked()

    def search(self, *_):
        """有关键词：按名字搜（选了季度就只要那一季的）；没关键词但选了季度：列出那一季的动画。"""
        kw = self.edit.text().strip()
        sea = self.f_season.currentData()
        if not kw and not sea:
            return
        what = f"「{kw}」" if kw else ""
        if sea:
            what += f"（{season_label(sea)}开播）" if kw else f"{season_label(sea)}开播的动画"
        self.status.setText(f"正在搜索{what}…")
        self._gen += 1
        gen = self._gen
        self.list.clear()
        self.ok.setEnabled(False)
        for t in self._thumb_tasks:
            t.cancelled = True
        self._thumb_tasks = []
        limit = 20 if kw else 60
        client = self.client
        run_async(lambda: client.search(kw, limit=limit, season=sea) if sea else client.search(kw),
                  lambda res: self._show_results(rank_candidates({"title": kw, "season": self.season}, res)
                                                 if self.season and kw else res, gen),
                  lambda e: gen == self._gen and self.status.setText("搜索失败：" + e.splitlines()[-1]))

    def _show_results(self, results, gen):
        if gen != self._gen:             # 已经有更新的搜索了
            return
        self.list.clear()
        self.results = results
        if not results:
            self.status.setText("没有找到结果。可以换个关键词（比如日文原名）或换个季度再试。")
            return
        placeholder = QPixmap(THUMB)
        placeholder.fill(Qt.lightGray)
        for row, subj in enumerate(results):
            it = QListWidgetItem(QIcon(placeholder), describe(subj))
            it.setData(Qt.UserRole, subj)
            it.setSizeHint(QSize(0, THUMB.height() + 10))
            self.list.addItem(it)
            imgs = subj.get("images") or {}
            url = imgs.get("grid") or imgs.get("small") or subj.get("image")
            if url:
                # 注意：不能把列表项对象本身交给后台回调——重新搜索时列表会清空、列表项被释放，
                # 回调再用它就会闪退。这里只记下「第几次搜索的第几行、是哪个条目」，回来时再查。
                self._thumb_tasks.append(run_async(
                    lambda u=url: self.client.download(u),
                    lambda data, g=gen, r=row, sid=subj.get("id"): self._set_thumb(g, r, sid, data)))
        self._apply_filter()

    def _matches(self, subj):
        fmt = self.f_format.currentData()
        if fmt and guess_format(subj) != fmt:
            return False
        words = self.f_tag.text().strip().lower().split()
        if words:
            hay = " ".join(subject_tag_names(subj)).lower()
            if not all(w in hay for w in words):
                return False
        return True

    def _apply_filter(self):
        """体裁 / 题材在已经搜到的结果里筛（不用重新搜索）。"""
        shown = 0
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            ok = self._matches(it.data(Qt.UserRole) or {})
            it.setHidden(not ok)
            if ok:
                shown += 1
                first = first if first is not None else i
        if not self.results:
            return
        if shown == len(self.results):
            self.status.setText(f"找到 {shown} 个结果，双击或选中后点「{self.ok.text()}」")
        else:
            self.status.setText(f"找到 {len(self.results)} 个结果，筛选后剩 {shown} 个")
        cur = self.list.currentItem()
        if cur is None or cur.isHidden():
            self.list.setCurrentRow(first if first is not None else -1)

    def _set_thumb(self, gen, row, sid, data):
        if gen != self._gen:
            return
        item = self.list.item(row)
        if item is None or (item.data(Qt.UserRole) or {}).get("id") != sid:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            item.setIcon(QIcon(pm.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _on_current(self, cur, _prev):
        self.ok.setEnabled(cur is not None)
        if cur is None:
            self.season_hint.setText("")
            return
        subj = cur.data(Qt.UserRole)
        s = season_from_date(subj.get("date"))
        tag = season_tag(s) if s else ""
        extra = f"  · {tag}" if tag else ""
        self.season_hint.setText(f"开播季度：{season_label(s)}（{s}）{extra}" if s else "开播日期未知")

    def accept_selected(self):
        it = self.list.currentItem()
        if it is None:
            return
        self.selected = it.data(Qt.UserRole)
        self.accept()

    def _manual(self):
        self.manual = True
        self.accept()
