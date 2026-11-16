"""可编辑的配置文件 data/config.json：评分标准（名作之壁）、观看状态、体裁。

这些不是「数据」而是「规则」：改了以后，表格里的评级、颜色、下拉选项都跟着变。
文件是普通 JSON，也可以直接用记事本改（改完在程序里点「重新读取」或重启）。
"""
import json
import logging
import math
import os
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 和原来 Excel 里「名作之壁」一样的默认评分标准
DEFAULT_RUBRIC = [
    {"score": "10", "level": "标杆", "color": "#C2185B"},
    {"score": "(9,10)", "level": "神作", "color": "#C2185B"},
    {"score": "9", "level": "神作之壁", "color": "#C2185B"},
    {"score": "(8,9)", "level": "名作", "color": "#E65100"},
    {"score": "8", "level": "名作之壁", "color": "#E65100"},
    {"score": "(7,8)", "level": "上乘", "color": "#2E7D32"},
    {"score": "7", "level": "佳作分水岭", "color": "#2E7D32"},
    {"score": "(6,7)", "level": "黄金厕纸", "color": "#00838F"},
    {"score": "6", "level": "及格线", "color": "#00838F"},
    {"score": "(5,6)", "level": "厕纸", "color": "#6B7280"},
    {"score": "5", "level": "粪作线", "color": "#6B7280"},
    {"score": "(4,5)", "level": "粪作", "color": "#8E24AA"},
    {"score": "小于等于4", "level": "恶心人之作", "color": "#8E24AA"},
]
# badge：海报墙封面左下角要不要显示这个状态（「看完」太多了，默认不显示）
DEFAULT_STATUSES = [
    {"name": "Done", "color": "#16A34A", "badge": False},
    {"name": "ing", "color": "#E8707E", "badge": True},
    {"name": "DL", "color": "#9CA3AF", "badge": True},
    {"name": "补番", "color": "#3B82F6", "badge": True},
]
DEFAULT_FORMATS = ["季番", "半年番", "年番", "剧场版", "泡面番", "ova"]
FALLBACK_COLOR = "#6B7280"
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


# ---------- 分数区间 ----------
_NUM = r"(-?\d+(?:\.\d+)?)"


def parse_range(text):
    """「名作之壁」里的分数写法 → (下限, 含下限, 上限, 含上限)。认不出返回 None。

    支持：9 / 9.5（正好这个分）、(8,9) [8,9) (8,9]（开闭区间，全角括号逗号也行）、
    8-9 / 8~9（含两端）、小于等于4 / <=4 / ≤4 / 4以下、小于4 / <4、
    大于等于9 / >=9 / ≥9 / 9以上、大于9 / >9。
    """
    t = (text or "").strip()
    if not t:
        return None
    t = (t.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
         .replace("，", ",").replace("～", "~").replace("－", "-").replace("≤", "<=").replace("≥", ">=")
         .replace("小于等于", "<=").replace("大于等于", ">=").replace("不超过", "<=")
         .replace("小于", "<").replace("大于", ">").replace(" ", ""))
    m = re.fullmatch(_NUM, t)
    if m:
        v = float(m.group(1))
        return (v, True, v, True)
    m = re.fullmatch(r"([(\[])" + _NUM + "," + _NUM + r"([)\]])", t)
    if m:
        lo, hi = float(m.group(2)), float(m.group(3))
        return (min(lo, hi), m.group(1) == "[", max(lo, hi), m.group(4) == "]")
    m = re.fullmatch(_NUM + r"[~\-到至]" + _NUM, t)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), True, max(lo, hi), True)
    m = re.fullmatch(r"(<=|<|>=|>)" + _NUM + r"分?", t)
    if m:
        op, v = m.group(1), float(m.group(2))
        if op.startswith("<"):
            return (-math.inf, False, v, op == "<=")
        return (v, op == ">=", math.inf, False)
    m = re.fullmatch(_NUM + r"分?(及?以下|及?以上)", t)
    if m:
        v = float(m.group(1))
        if m.group(2).endswith("下"):
            return (-math.inf, False, v, True)
        return (v, True, math.inf, False)
    return None


def in_range(score, rng):
    lo, lo_in, hi, hi_in = rng
    if score < lo or (score == lo and not lo_in):
        return False
    if score > hi or (score == hi and not hi_in):
        return False
    return True


def _legacy_color(score):
    """配置里没写颜色的档位：按原来的配色给一个。"""
    if score is None:
        return FALLBACK_COLOR
    if score >= 9:
        return "#C2185B"
    if score >= 8:
        return "#E65100"
    if score >= 7:
        return "#2E7D32"
    if score >= 6:
        return "#00838F"
    if score >= 5:
        return "#6B7280"
    return "#8E24AA"


def _probe(rng):
    """区间里有代表性的一个分数（用来给没写颜色的档位配默认色）。"""
    lo, _, hi, _ = rng
    if math.isinf(lo):
        return hi
    if math.isinf(hi):
        return lo
    return (lo + hi) / 2


# ---------- 配置本体 ----------
class AppConfig:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = {}
        self.load()

    # 读 / 写
    def load(self):
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    raise ValueError("不是 JSON 对象")
            except (OSError, ValueError) as e:
                log.warning("配置文件读不了，先用默认值：%s", e)
                bad = self.path.with_suffix(".broken.json")
                try:
                    self.path.replace(bad)
                    log.warning("原文件改名为 %s", bad.name)
                except OSError:
                    pass
                data = {}
        self.data = self._normalized(data)
        self._compile()
        return self

    def save(self):
        self.data = self._normalized(self.data)
        self._compile()
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)          # 先写临时文件再替换，写到一半断电也不会坏

    def exists(self):
        return self.path.exists()

    @staticmethod
    def _normalized(data):
        out = dict(data)
        rubric = []
        for r in data.get("rubric") or []:
            if not isinstance(r, dict):
                continue
            row = {k: str(r.get(k) or "").strip() for k in ("score", "level", "example", "description", "color")}
            if row["color"] and not _HEX.fullmatch(row["color"]):
                row["color"] = ""
            if any(row[k] for k in ("score", "level", "example", "description")):
                rubric.append(row)
        out["rubric"] = rubric if "rubric" in data else [dict(r, example="", description="")
                                                         for r in DEFAULT_RUBRIC]
        statuses, seen = [], set()
        for s in data.get("statuses") if "statuses" in data else DEFAULT_STATUSES:
            if isinstance(s, str):
                s = {"name": s}
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            color = str(s.get("color") or "").strip()
            statuses.append({"name": name, "color": color if _HEX.fullmatch(color) else FALLBACK_COLOR,
                             "badge": bool(s.get("badge", True))})
        out["statuses"] = statuses
        formats, seen = [], set()
        for f in data.get("formats") if "formats" in data else DEFAULT_FORMATS:
            f = str(f or "").strip()
            if f and f not in seen:
                seen.add(f)
                formats.append(f)
        out["formats"] = formats
        return out

    def _compile(self):
        self._tiers = []
        for r in self.data["rubric"]:
            rng = parse_range(r.get("score"))
            if rng is None or not r.get("level"):
                continue
            color = r.get("color") or _legacy_color(_probe(rng))
            self._tiers.append((rng, r["level"], color))
        self._status = {s["name"]: s for s in self.data["statuses"]}

    # 评分标准
    def rubric(self):
        return [dict(r) for r in self.data["rubric"]]

    def set_rubric(self, rows, save=True):
        """rows 来自 Excel 导入或编辑窗口；Excel 里没有颜色列，导入时保留同一等级原来的颜色。"""
        old = {r.get("level"): r.get("color") for r in self.data.get("rubric", [])}
        new = []
        for r in rows:
            r = dict(r)
            if not r.get("color") and old.get(r.get("level")):
                r["color"] = old[r.get("level")]
            new.append(r)
        self.data["rubric"] = new
        if save:
            self.save()
        else:
            self.data = self._normalized(self.data)
            self._compile()

    def unparsed_rubric_rows(self):
        return [r for r in self.data["rubric"] if r.get("score") and parse_range(r["score"]) is None]

    def tier(self, score):
        """(等级名, 颜色)。分数不在任何档位里 → ("", 默认色)。"""
        if score is None:
            return "", FALLBACK_COLOR
        try:
            s = float(score)
        except (TypeError, ValueError):
            return "", FALLBACK_COLOR
        exact = [t for t in self._tiers if t[0][0] == t[0][2] and in_range(s, t[0])]
        for rng, name, color in exact + self._tiers:
            if in_range(s, rng):
                return name, color
        return "", _legacy_color(s)

    # 观看状态
    def statuses(self):
        return [dict(s) for s in self.data["statuses"]]

    def status_names(self):
        return [s["name"] for s in self.data["statuses"]]

    def status_info(self, name):
        """未在配置里的状态（比如以前填的）也能显示：用灰色。"""
        if not name:
            return None
        return self._status.get(name) or {"name": name, "color": FALLBACK_COLOR, "badge": True}

    def set_statuses(self, items, save=True):
        self.data["statuses"] = items
        if save:
            self.save()

    # 体裁
    def formats(self):
        return list(self.data["formats"])

    def set_formats(self, items, save=True):
        self.data["formats"] = items
        if save:
            self.save()


_by_path = {}
_lock = threading.Lock()


def for_path(path):
    """同一个文件只读一次，大家共用同一个对象（改了马上处处生效）。"""
    key = str(Path(path).resolve())
    with _lock:
        if key not in _by_path:
            _by_path[key] = AppConfig(path)
        return _by_path[key]


def current():
    """程序当前数据文件夹里的配置。"""
    from . import config
    return for_path(config.DATA_DIR / "config.json")
