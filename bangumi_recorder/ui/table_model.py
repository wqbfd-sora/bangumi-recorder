"""表格模型：同一份数据，按视图显示不同的列。"""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from ..db import FIELD_LABELS, fmt_score, score_tier, season_label
from .style import score_color

# 视图 → 列
SEASON_COLS = ["title", "score", "status", "format", "tags", "weekday", "studio", "director",
               "series_comp", "short_name", "episodes", "air_date", "comment", "bgm"]
BACKLOG_COLS = ["title", "score", "status", "season", "format", "tags", "studio", "comment", "bgm"]
RANK_COLS = ["rank", "title", "score", "tier", "season", "format", "studio", "tags", "status",
             "bgm_score"]
ALL_COLS = ["season", "format", "title", "short_name", "studio", "tags", "status", "episodes",
            "air_date", "comment", "score", "bgm_score", "bgm"]

HEADERS = dict(FIELD_LABELS)
HEADERS.update({"rank": "#", "tier": "评级", "season": "季度", "bgm": "BGM",
                "bgm_score": "BGM 评分", "air_date": "开播", "episodes": "集数", "score": "终评"})

CENTER = {"rank", "score", "tier", "season", "format", "weekday", "status", "episodes", "air_date",
          "bgm_score", "bgm"}
SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
ROW_ROLE = Qt.UserRole + 3      # 整行数据（dict），海报墙用
RANK_ROLE = Qt.UserRole + 4     # 总榜里的名次


class AnimeModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.cols = SEASON_COLS
        self.ranks = {}

    def set_data(self, rows, cols):
        self.beginResetModel()
        self.rows = rows
        self.cols = cols
        self.ranks = {}
        if "rank" in cols:
            ordered = sorted((r for r in rows if r.get("score") is not None), key=lambda r: -r["score"])
            prev, rank = None, 0
            for i, r in enumerate(ordered, 1):
                if r["score"] != prev:
                    rank, prev = i, r["score"]
                self.ranks[r["id"]] = rank
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS.get(self.cols[section], self.cols[section])
        return None

    def row_id(self, row):
        return self.rows[row]["id"]

    def _display(self, r, c):
        if c == "score":
            return fmt_score(r.get("score")) if r.get("score") is not None else (r.get("score_note") or "")
        if c == "tier":
            return score_tier(r.get("score"))
        if c == "rank":
            rk = self.ranks.get(r["id"])
            return str(rk) if rk else ""
        if c == "bgm_score":
            return f"{r['bgm_score']:.1f}" if r.get("bgm_score") else ""
        if c == "bgm":
            return "●" if r.get("bgm_id") else ""
        if c == "season":
            return season_label(r.get("season")) if r.get("season") else ""
        v = r.get(c)
        return "" if v is None else str(v)

    def _sort_key(self, r, c):
        if c in ("score", "bgm_score"):
            v = r.get(c)
            return float(v) if v is not None else -999.0
        if c == "rank":
            return self.ranks.get(r["id"], 99999)
        if c == "tier":
            v = r.get("score")
            return float(v) if v is not None else -999.0
        if c == "episodes":
            v = str(r.get("episodes") or "")
            num = "".join(ch for ch in v.split("+")[0] if ch.isdigit())
            return int(num) if num else -1
        if c == "season":
            s = r.get("season") or ""
            if s.endswith("年") and s[:-1].isdigit():
                return s[:-1].zfill(2) + "00"
            return s
        if c == "bgm":
            return 1 if r.get("bgm_id") else 0
        v = r.get(c)
        return "" if v is None else str(v)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = self.cols[index.column()]
        if role == Qt.DisplayRole:
            return self._display(r, c)
        if role == SORT_ROLE:
            return self._sort_key(r, c)
        if role == ID_ROLE:
            return r["id"]
        if role == ROW_ROLE:
            return r
        if role == RANK_ROLE:
            return self.ranks.get(r["id"])
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter if c in CENTER else Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if c in ("score", "tier"):
                return QBrush(score_color(r.get("score")))
            if c == "bgm":
                return QBrush(QColor("#E8707E"))
            if c == "status" and r.get("status") == "DL":
                return QBrush(QColor("#9CA3AF"))
        if role == Qt.FontRole and c in ("score", "title", "rank"):
            f = QFont()
            f.setBold(c != "title" or r.get("score") is not None and r["score"] >= 9)
            return f
        if role == Qt.ToolTipRole:
            if c == "title" or index.column() == 0:
                tips = [r.get("title") or ""]
                if r.get("bgm_name"):
                    tips.append(r["bgm_name"])
                if r.get("comment"):
                    tips.append("个人评价：" + r["comment"])
                return "\n".join(tips)
            if c == "bgm":
                return f"Bangumi #{r['bgm_id']}" if r.get("bgm_id") else "未关联 Bangumi"
            text = self._display(r, c)
            return text if len(text) > 12 else None
        return None


class AnimeFilter(QSortFilterProxyModel):
    SEARCH_KEYS = ("title", "romaji", "short_name", "bgm_name", "bgm_name_cn", "studio", "tags", "comment",
                   "director", "series_comp")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.format = None
        self.status = None
        self.scored_only = False
        self.setSortRole(SORT_ROLE)

    def set_filters(self, text=None, fmt=None, status=None, scored_only=None):
        if text is not None:
            self.text = text.strip().lower()
        if fmt is not None:
            self.format = fmt or None
        if status is not None:
            self.status = status or None
        if scored_only is not None:
            self.scored_only = scored_only
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        r = self.sourceModel().rows[source_row]
        if self.scored_only and r.get("score") is None:
            return False
        if self.format and (r.get("format") or "") != self.format:
            return False
        if self.status:
            st = r.get("status") or ""
            if self.status == "（空）":
                if st:
                    return False
            elif st != self.status:
                return False
        if self.text:
            hay = " ".join(str(r.get(k) or "") for k in self.SEARCH_KEYS).lower()
            if not all(word in hay for word in self.text.split()):
                return False
        return True
