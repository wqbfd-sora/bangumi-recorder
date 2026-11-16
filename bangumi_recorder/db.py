"""SQLite 数据层。

一部作品 = anime 表里的一行。季度表、补番、总榜都只是这张表的不同视图，
所以评分只存一份，不再需要「汇总」和季度表之间的同步。
"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import appconfig, config

# (列名, Excel 里的表头, 类型)。顺序 = 季度表里的列顺序。
FIELDS = [
    ("format", "体裁", "TEXT"),
    ("tags", "题材", "TEXT"),
    ("weekday", "星期", "TEXT"),
    ("studio", "制作公司", "TEXT"),
    ("director", "监督", "TEXT"),
    ("series_comp", "系列构成", "TEXT"),
    ("title", "名称", "TEXT"),
    ("short_name", "简称", "TEXT"),
    ("status", "状态", "TEXT"),
    ("episodes", "正片集数", "TEXT"),
    ("air_date", "开播时间", "TEXT"),
    ("comment", "个人评价", "TEXT"),
    ("score", "终评", "REAL"),
    ("romaji", "罗马音", "TEXT"),
    ("bgm_name", "日文原名", "TEXT"),
]
FIELD_LABELS = {k: label for k, label, _ in FIELDS}
FIELD_LABELS.update({
    "season": "季度",
    "is_backlog": "补番",
    "score_note": "评分备注",
    "bgm_id": "Bangumi ID",
    "bgm_name": "日文原名",
    "romaji": "罗马音",
    "bgm_name_cn": "Bangumi 中文名",
    "bgm_score": "Bangumi 评分",
    "bgm_rank": "Bangumi 排名",
    "cover_url": "封面",
    "summary": "简介",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS anime (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    romaji        TEXT,              -- 日文原名的罗马音（官方/AniList 写法优先）
    short_name    TEXT,
    season        TEXT,              -- 放送季度：'2507' / '16年' / NULL
    is_backlog    INTEGER NOT NULL DEFAULT 0,   -- 1 = 补番
    format        TEXT,
    tags          TEXT,
    weekday       TEXT,
    studio        TEXT,
    director      TEXT,
    series_comp   TEXT,
    status        TEXT,
    episodes      TEXT,
    air_date      TEXT,              -- ISO 日期 yyyy-mm-dd
    comment       TEXT,
    score         REAL,              -- 个人评分，原样保存（可为小数/负数）
    score_note    TEXT,              -- 非数字的评分格内容，如 '\\'
    sort_order    INTEGER NOT NULL DEFAULT 0,
    bgm_id        INTEGER,
    bgm_name      TEXT,
    bgm_name_cn   TEXT,
    bgm_score     REAL,
    bgm_rank      INTEGER,
    cover_url     TEXT,
    summary       TEXT,
    extra         TEXT,              -- JSON：导入时无处安放的原始值
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anime_season ON anime(season);
CREATE INDEX IF NOT EXISTS idx_anime_bgm ON anime(bgm_id);

CREATE TABLE IF NOT EXISTS rubric (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    score       TEXT,
    level       TEXT,
    example     TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

COLUMNS = ["id", "title", "romaji", "short_name", "season", "is_backlog", "format", "tags", "weekday",
           "studio", "director", "series_comp", "status", "episodes", "air_date", "comment",
           "score", "score_note", "sort_order", "bgm_id", "bgm_name", "bgm_name_cn", "bgm_score",
           "bgm_rank", "cover_url", "summary", "extra", "created_at", "updated_at"]
EDITABLE = [c for c in COLUMNS if c not in ("id", "created_at", "updated_at")]


def now():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path=None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.cfg = appconfig.for_path(self.path.parent / "config.json")
        if not self.cfg.exists():
            self._seed_config()

    def _seed_config(self):
        """第一次用配置文件：把数据库里原来的评分标准、表里出现过的状态和体裁搬进去。"""
        old = [dict(r) for r in self.conn.execute("SELECT * FROM rubric ORDER BY id")]
        if old:
            self.cfg.set_rubric(old, save=False)
        statuses = self.cfg.statuses()
        names = {s["name"] for s in statuses}
        for (st,) in self.conn.execute("SELECT DISTINCT status FROM anime WHERE status IS NOT NULL"):
            if st and st not in names:
                statuses.append({"name": st, "color": appconfig.FALLBACK_COLOR, "badge": True})
                names.add(st)
        self.cfg.set_statuses(statuses, save=False)
        formats = self.cfg.formats()
        for (fm,) in self.conn.execute("SELECT DISTINCT format FROM anime WHERE format IS NOT NULL"):
            if fm and fm not in formats:
                formats.append(fm)
        self.cfg.set_formats(formats, save=False)
        try:
            self.cfg.save()
        except OSError:
            pass

    def _migrate(self):
        """给旧数据库补上新加的列。"""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(anime)")}
        if "romaji" not in have:
            self.conn.execute("ALTER TABLE anime ADD COLUMN romaji TEXT")

    def close(self):
        self.conn.close()

    # ---------- anime ----------
    def all(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM anime ORDER BY is_backlog, season DESC, sort_order, id")]

    def get(self, anime_id):
        r = self.conn.execute("SELECT * FROM anime WHERE id = ?", (anime_id,)).fetchone()
        return dict(r) if r else None

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0]

    def add(self, data, commit=True):
        data = {k: v for k, v in data.items() if k in EDITABLE}
        if not str(data.get("title") or "").strip():
            raise ValueError("作品名不能为空")
        if "sort_order" not in data:
            data["sort_order"] = self._next_order(data.get("season"), data.get("is_backlog", 0))
        data["created_at"] = data["updated_at"] = now()
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO anime ({cols}) VALUES ({marks})", list(data.values()))
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def update(self, anime_id, data, commit=True):
        data = {k: v for k, v in data.items() if k in EDITABLE}
        if not data:
            return
        if "title" in data and not str(data["title"] or "").strip():
            raise ValueError("作品名不能为空")
        data["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in data)
        self.conn.execute(f"UPDATE anime SET {sets} WHERE id = ?", [*data.values(), anime_id])
        if commit:
            self.conn.commit()

    def delete(self, anime_id):
        self.conn.execute("DELETE FROM anime WHERE id = ?", (anime_id,))
        self.conn.commit()

    def _next_order(self, season, is_backlog):
        r = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM anime WHERE season IS ? AND is_backlog = ?",
            (season, int(bool(is_backlog)))).fetchone()
        return r[0]

    def seasons(self):
        """季度列表（不含补番），新的在前。"""
        rows = self.conn.execute(
            "SELECT season, COUNT(*) FROM anime WHERE is_backlog = 0 AND season IS NOT NULL "
            "GROUP BY season ORDER BY season DESC").fetchall()
        return [(r[0], r[1]) for r in rows]

    def find_duplicate(self, title, season, exclude_id=None):
        r = self.conn.execute(
            "SELECT id FROM anime WHERE title = ? AND season IS ? AND id IS NOT ?",
            (title, season, exclude_id)).fetchone()
        return r[0] if r else None

    # ---------- rubric ----------
    # 评分标准存在配置文件 config.json 里（是「规则」不是「数据」），见 appconfig.py
    def rubric(self):
        return self.cfg.rubric()

    def set_rubric(self, rows, commit=True):
        self.cfg.set_rubric(rows, save=True)

    def replace_all(self, rows, rubric=None):
        """整体替换所有作品（导入用）。在同一个事务里完成：中途出错会全部回滚，原数据原封不动。
        评分标准在数据库提交成功之后才写进配置文件。"""
        with self.conn:
            self.conn.execute("DELETE FROM anime")
            for item in rows:
                self.add(item, commit=False)
        if rubric:
            self.set_rubric(rubric)

    # ---------- settings ----------
    def get_setting(self, key, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        self.conn.commit()

    # ---------- backup ----------
    def backup(self, tag="backup", keep=40):
        """把当前数据库复制一份到 data/backups/，返回备份路径。只保留最近 keep 份。"""
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = config.BACKUP_DIR / f"anime_{tag}_{datetime.now():%Y%m%d_%H%M%S}.db"
        bk = sqlite3.connect(dst)
        with bk:
            self.conn.backup(bk)
        bk.close()
        olds = sorted(config.BACKUP_DIR.glob("anime_*.db"), key=lambda p: p.stat().st_mtime)
        for old in olds[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    def restore_from(self, path):
        """用一份备份覆盖当前数据库（先自动备份当前的）。返回刚做的那份备份。"""
        safety = self.backup("before_restore")
        src = sqlite3.connect(path)
        try:
            src.execute("SELECT COUNT(*) FROM anime").fetchone()   # 确认是本程序的数据库
            src.backup(self.conn)
        finally:
            src.close()
        self._migrate()
        self.conn.commit()
        return safety


def extra_of(row):
    try:
        return json.loads(row.get("extra") or "{}")
    except (TypeError, ValueError):
        return {}


def season_label(season, is_backlog=False):
    """'2507' → '2025 年 7 月'；'16年' → '2016 年'。"""
    if not season:
        return "未知季度"
    if len(season) == 4 and season.isdigit():
        yy, mm = int(season[:2]), int(season[2:])
        return f"20{yy:02d} 年 {mm} 月"
    if season.endswith("年") and season[:-1].isdigit():
        yy = int(season[:-1])
        year = 2000 + yy if yy < 70 else 1900 + yy
        return f"{year} 年"
    return season


def season_from_date(date_str):
    """'2025-07-05' → '2507'（按 1/4/7/10 月归季）。

    季末最后十来天开播的（如 9 月 29 日）算进下一季，和原表的习惯一致。
    """
    if not date_str or len(date_str) < 7:
        return None
    try:
        y, m = int(date_str[:4]), int(date_str[5:7])
        d = int(date_str[8:10]) if len(date_str) >= 10 else 1
    except ValueError:
        return None
    if m % 3 == 0 and d >= 20:
        m += 1
        if m > 12:
            y, m = y + 1, 1
    q = (m - 1) // 3 * 3 + 1
    return f"{y % 100:02d}{q:02d}"


def current_season(today=None):
    """按电脑的本机日期算出「当季」，如 2026-11-15 → '2610'。"""
    today = today or date.today()
    return season_from_date(today.isoformat())


def is_quarter(season):
    return bool(season) and len(season) == 4 and season.isdigit() and season[2:] in ("01", "04", "07", "10")


def shift_season(season, n):
    """'2610' 往后 n 季（n 可为负）。"""
    yy, mm = int(season[:2]), int(season[2:])
    idx = yy * 4 + (mm - 1) // 3 + n
    return f"{(idx // 4) % 100:02d}{(idx % 4) * 3 + 1:02d}"


def season_compare(season, today=None):
    """和当季比：-1 已播完的季度，0 当季，1 未来（定档还没开播）；不是 YYMM 格式的返回 None。"""
    if not is_quarter(season):
        return None
    cur = current_season(today)
    return (season > cur) - (season < cur)


def season_date_range(season):
    """这一季对应的开播日期范围 [起, 止)，和 season_from_date 的归季规则一致：
    季度开始前一个月 20 日起，到季度最后一个月 20 日前（如 2610：2026-09-20 ~ 2026-12-20）。"""
    yy, mm = int(season[:2]), int(season[2:])
    y = 2000 + yy
    start = date(y, mm, 1) - timedelta(days=1)
    start = start.replace(day=20)
    end_m = mm + 2
    end = date(y, end_m, 20)
    return start, end


# ---------- 评分等级（来自配置文件里的「名作之壁」） ----------
def score_tier(score):
    if score is None:
        return ""
    return appconfig.current().tier(score)[0]


def fmt_score(score):
    if score is None:
        return ""
    s = float(score)
    return str(int(s)) if s.is_integer() else f"{s:g}"
