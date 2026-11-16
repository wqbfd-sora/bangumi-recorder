"""Excel 记录表的导入与导出（表格的格式沿用作者原来手动记录用的表：每季一张表 + 补番 + 汇总 + 名作之壁）。

导入只读取原文件，永远不会修改它。
评分的取法与原来的同步规则一致：
  - 季度表/补番里是数字 → 用这个数字；
  - 是链接「汇总」的公式 → 去汇总按 作品名+季度 取分；
  - 是别的文字（如 '\\'）→ 不算分，原样存进「评分备注」。
"""
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .bangumi import SUBJECT_URL
from .db import FIELDS, extra_of, fmt_score

SUMMARY = "汇总"
BUFAN = "补番"
RUBRIC = "名作之壁"
SKIP = {"模板"}
SEASON_RE = re.compile(r"^\d{4}$")

# 表头别名 → 字段
HEADER_MAP = {
    "体裁": "format", "题材": "tags", "题材/TAG": "tags", "星期": "weekday",
    "制作公司": "studio", "监督": "director", "系列构成": "series_comp",
    "名称": "title", "作品名": "title", "简称": "short_name",
    "状态": "status", "观看状态": "status", "正片集数": "episodes", "开播时间": "air_date",
    "个人评价": "comment", "个人评语": "comment", "终评": "score", "/10": "score",
    "年份": "_year", "季度": "_quarter",
    "罗马音": "romaji", "日文原名": "bgm_name",
    # 本程序导出时附带的 Bangumi 关联数据（导回时恢复）
    "Bangumi ID": "bgm_id", "Bangumi 中文名": "bgm_name_cn", "Bangumi 评分": "bgm_score",
    "Bangumi 排名": "bgm_rank", "封面链接": "cover_url", "简介": "summary",
    "评分明细": "_breakdown", "附加信息": "_extra",
}
BGM_COLS = [("bgm_id", "Bangumi ID"), ("bgm_name_cn", "Bangumi 中文名"), ("bgm_score", "Bangumi 评分"),
            ("bgm_rank", "Bangumi 排名"), ("cover_url", "封面链接"), ("summary", "简介"),
            ("_breakdown", "评分明细"), ("_extra", "附加信息")]
UNKNOWN_SEASON_SHEET = "未知季度"
DERIVED_EXTRA = ("年份", "季度", "评分明细")     # 已经由其他列表示，不放进「附加信息」
YEAR_SHEET_RE = re.compile(r"^\d{2}年$")


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def breakdown_text(scores):
    """{'剧情': 8.5, '制作': 8} → '剧情 8.5 / 制作 8'"""
    return " / ".join(f"{k} {fmt_score(v)}" for k, v in (scores or {}).items()) or None


def parse_breakdown(text):
    out = {}
    for part in re.split(r"[/／,，;；]", str(text or "")):
        m = re.match(r"\s*(.+?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*$", part)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out
LINK_Q_RE = re.compile(r"汇总'?!\$[A-Z]+\$2:\$[A-Z]+\$\d+=\"([^\"]*)\"")


def _clean(v):
    if isinstance(v, str):
        v = re.sub(r"\s+", " ", v.replace("\xa0", " ")).strip()
        return v or None
    return v


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _text(v):
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _multiline(v):
    """简介、个人评价这类长文本：保留换行，只去掉首尾空白。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").strip()
        return v or None
    return _text(v)


def bufan_season(year, quarter):
    try:
        yy = int(year) % 100
    except (TypeError, ValueError):
        return None
    try:
        return f"{yy:02d}{int(quarter):02d}"
    except (TypeError, ValueError):
        return f"{yy:02d}年"


def convert_air_date(v, season):
    """开播时间在表里有三种写法：日期、Excel 日期序号、'月.日' 小数（如 1.2）。"""
    if v is None:
        return None, False
    if isinstance(v, datetime):
        return v.date().isoformat(), False
    if isinstance(v, date):
        return v.isoformat(), False
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and v > 1000):
        return (datetime(1899, 12, 30) + timedelta(days=int(v))).date().isoformat(), False
    if isinstance(v, float) and season and SEASON_RE.match(season):
        m_str, _, d_str = repr(v).partition(".")
        try:
            year = 2000 + int(season[:2])
            if int(m_str) - int(season[2:]) > 6:   # 例如 2501 季度里写 12.28 → 上一年
                year -= 1
            return date(year, int(m_str), int(d_str or 1)).isoformat(), True
        except ValueError:
            return None, True
    if isinstance(v, str):
        m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", v)
        if m:
            return date(*map(int, m.groups())).isoformat(), False
    return None, True


class ImportReport:
    def __init__(self):
        self.items = defaultdict(list)
        self.count = 0
        self.scored = 0
        self.linked = 0

    def add(self, title, text):
        self.items[title].append(text)

    def text(self):
        lines = [f"共导入 {self.count} 部作品，其中 {self.scored} 部有评分。"]
        if self.linked:
            lines.append(f"识别到 {self.linked} 部作品的 Bangumi 关联数据（关联、封面、Bangumi 评分、"
                         "评分明细等已一起恢复，不用重新关联）。")
        for title, items in self.items.items():
            lines.append(f"\n【{title}】（{len(items)}）")
            lines.extend(f"  · {x}" for x in items)
        return "\n".join(lines)


def read_workbook(path):
    """读取 Excel 记录表，返回 (作品列表, 评分标准, 报告)。不修改文件。"""
    wb = openpyxl.load_workbook(path)  # 需要读公式，所以不用 data_only
    report = ImportReport()

    # ---- 汇总：作品名+季度 → 分数 ----
    summary = {}
    summary_extra = {}
    if SUMMARY in wb.sheetnames:
        ws = wb[SUMMARY]
        hdr = [_clean(c.value) for c in ws[1]]
        idx = {h: i for i, h in enumerate(hdr) if h}
        for r in ws.iter_rows(min_row=2, values_only=True):
            name = _clean(r[idx["作品名"]]) if "作品名" in idx else None
            if not name:
                continue
            q = _text(r[idx["季度"]]) if "季度" in idx else None
            key = (str(name), q)
            if key in summary:
                report.add("汇总里重复的作品（取第一行）", f"{name}（{q}）")
                continue
            summary[key] = _num(r[idx["个人评分"]]) if "个人评分" in idx else None
            summary_extra[key] = {h: _clean(r[i]) for h, i in idx.items()
                                  if h not in ("作品名", "个人评分", "季度")}
    used = set()

    rows = []
    linked = [0]
    own_export = [False]          # 是不是本程序导出的文件（带 Bangumi 列）
    sheets = [n for n in wb.sheetnames
              if SEASON_RE.match(n) or YEAR_SHEET_RE.match(n) or n == UNKNOWN_SEASON_SHEET]
    sheets.sort(reverse=True)
    if BUFAN in wb.sheetnames:
        sheets.append(BUFAN)

    for sheet in sheets:
        ws = wb[sheet]
        hdr = [_clean(c.value) for c in ws[1]]
        cols = {}
        unknown = {}
        for i, h in enumerate(hdr):
            if h is None:
                continue
            f = HEADER_MAP.get(h)
            if f and f not in cols:
                cols[f] = i
            else:
                unknown[h] = i
        if "title" not in cols:
            report.add("跳过的 sheet（找不到名称列）", sheet)
            continue
        is_backlog = sheet == BUFAN
        order = 0
        for r in ws.iter_rows(min_row=2, values_only=True):
            def g(f):
                return _clean(r[cols[f]]) if f in cols and cols[f] < len(r) else None

            def raw_cell(f):
                return r[cols[f]] if f in cols and cols[f] < len(r) else None
            title = _text(g("title"))
            if not title:
                continue
            order += 1
            if is_backlog:
                season = bufan_season(g("_year"), g("_quarter"))
            else:
                season = None if sheet == UNKNOWN_SEASON_SHEET else sheet
            extra = {}
            item = {"title": title, "season": season, "is_backlog": int(is_backlog),
                    "sort_order": order}
            for f in ("format", "tags", "weekday", "studio", "director", "series_comp",
                      "short_name", "status", "romaji", "bgm_name"):
                item[f] = _text(g(f))
            item["comment"] = _multiline(raw_cell("comment"))
            item["episodes"] = _text(g("episodes"))
            raw_date = g("air_date")
            item["air_date"], keep_raw = convert_air_date(raw_date, season)
            if keep_raw and raw_date is not None:
                extra["开播时间原值"] = raw_date if not isinstance(raw_date, (datetime, date)) else str(raw_date)

            # ---- 评分 ----
            raw = r[cols["score"]] if "score" in cols and cols["score"] < len(r) else None
            score, note = None, None
            if isinstance(raw, str) and raw.startswith("="):
                if "汇总" in raw:
                    m = LINK_Q_RE.search(raw.replace(" ", ""))
                    q = (m.group(1) or None) if m else season
                    key = (title, q) if (title, q) in summary else (title, season)
                    if key in summary:
                        score = summary[key]
                        used.add(key)
                else:
                    note = raw
                    report.add("评分格是其他公式（原样保存在评分备注）", f"{sheet} {title}")
            else:
                score = _num(raw)
                if score is None and _clean(raw) is not None:
                    note = str(_clean(raw))
                    report.add("评分不是数字（保存在评分备注）", f"{sheet} {title}: {note}")
                key = (title, season)
                if key in summary:
                    used.add(key)
                    if score is not None and summary[key] is not None and summary[key] != score:
                        report.add("季度表数字和汇总不同（以季度表为准）",
                                   f"{sheet} {title}: 汇总 {fmt_score(summary[key])} → {fmt_score(score)}")
                    elif score is None and note is None:
                        score = summary[key]
                elif score is not None:
                    report.add("季度表里有分、汇总里还没有", f"{sheet} {title}（{fmt_score(score)}）")
            item["score"], item["score_note"] = score, note

            # 汇总里有、季度表空着的列（如制作公司）
            sx = summary_extra.get((title, season)) or {}
            if not item.get("studio") and sx.get("制作公司"):
                item["studio"] = str(sx["制作公司"])
            if not item.get("format") and sx.get("体裁"):
                item["format"] = str(sx["体裁"])

            # ---- 本程序导出的 Bangumi 关联数据 ----
            if g("_extra"):
                try:
                    saved = json.loads(str(g("_extra")))
                    if isinstance(saved, dict):
                        for k in DERIVED_EXTRA:
                            saved.pop(k, None)
                        odd = saved.pop("原季度", None)
                        if odd and not item.get("season"):
                            item["season"] = str(odd)
                        extra = {**saved, **extra}
                except ValueError:
                    extra["附加信息"] = g("_extra")
            if g("_breakdown"):
                bd = parse_breakdown(g("_breakdown"))
                if bd:
                    extra["评分明细"] = bd
            item["bgm_id"] = _int(g("bgm_id"))
            item["bgm_rank"] = _int(g("bgm_rank"))
            item["bgm_score"] = _num(g("bgm_score"))
            for f in ("bgm_name_cn", "cover_url"):
                item[f] = _text(g(f))
            item["summary"] = _multiline(raw_cell("summary"))
            if item["bgm_id"]:
                linked[0] += 1

            for h, i in unknown.items():
                if i < len(r) and _clean(r[i]) is not None:
                    extra[h] = _clean(r[i]) if not isinstance(r[i], (datetime, date)) else str(r[i])
            item["extra"] = json.dumps(extra, ensure_ascii=False, default=str) if extra else None
            if item.get("season"):                       # 恢复了原季度写法的，也算在汇总里对上了
                used.add((title, _clean(item["season"])))
            if "_extra" in cols or "bgm_id" in cols:
                own_export[0] = True
            rows.append(item)

    # 汇总里有、但哪个季度表都没有的 → 也导入（放进补番）。
    # 本程序导出的文件里汇总只是季度表的副本，不需要这一步（否则写法特殊的会被重复导入）
    for key, sc in summary.items():
        if key in used or own_export[0]:
            continue
        title, q = key
        sx = summary_extra.get(key) or {}
        rows.append({"title": title, "season": q, "is_backlog": 1, "sort_order": 10000 + len(rows),
                     "score": sc, "format": _text(sx.get("体裁")), "studio": _text(sx.get("制作公司")),
                     "extra": json.dumps({"来源": "仅在汇总中"}, ensure_ascii=False)})
        report.add("只在汇总里出现，已放进补番", f"{title}（{q}）{fmt_score(sc)}")

    # ---- 名作之壁（评分标准）----
    rubric = []
    if RUBRIC in wb.sheetnames:
        ws = wb[RUBRIC]
        for r in ws.iter_rows(min_row=3, values_only=True):
            vals = [_clean(x) for x in r[:4]]
            if any(v is not None for v in vals):
                rubric.append({"score": _text(vals[0]), "level": _text(vals[1]),
                               "example": _text(vals[2]), "description": _text(vals[3])})

    report.count = len(rows)
    report.scored = sum(1 for x in rows if x.get("score") is not None)
    report.linked = linked[0]
    return rows, rubric, report


def import_into(db, path, replace=False):
    """把 Excel 导入数据库。replace=True 时先备份再清空。返回报告。"""
    rows, rubric, report = read_workbook(path)
    backup = None
    if db.count():
        if not replace:
            raise RuntimeError("数据库里已经有数据")
        backup = db.backup("before_import")
    db.replace_all(rows, rubric)          # 一个事务：失败就全部回滚，原数据不受影响
    if backup:
        report.add("导入前已备份旧数据库", str(backup))
    return report


# ---------------- 导出 ----------------
EXPORT_COLS = [label for _, label, _ in FIELDS]
BGM_LABELS = [label for _, label in BGM_COLS]
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
BGM_FILL = PatternFill("solid", fgColor="FCE4EC")       # Bangumi 关联数据的表头用粉色区分
LINK_FONT = Font(color="C9505E", underline="single")


def _write_header(ws, headers, widths):
    for j, h in enumerate(headers, 1):
        c = ws.cell(1, j, h)
        c.font = Font(bold=True)
        c.fill = BGM_FILL if h in BGM_LABELS else HEAD_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = widths.get(h, 12)
    ws.freeze_panes = "A2"


def _clean_text(value):
    """Excel 单元格不能放控制字符（网页复制来的文字里偶尔会有），单格最多 32767 字。"""
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)[:32767]
    return value


def _safe_cell(ws, row, col, value):
    """写单元格。以 = 开头的文字（比如个人评价写了「=神作」）要存成文字，否则 Excel 会当公式算。"""
    value = _clean_text(value)
    c = ws.cell(row, col, value)
    if isinstance(value, str) and value.startswith("="):
        c.data_type = "s"
    return c


def _cell_value(row, field):
    if field == "_breakdown":
        return breakdown_text(extra_of(row).get("评分明细"))
    if field == "_extra":
        ex = {k: v for k, v in extra_of(row).items() if k not in DERIVED_EXTRA}
        return json.dumps(ex, ensure_ascii=False, default=str) if ex else None
    v = row.get(field)
    if field == "score":
        return v if v is not None else row.get("score_note")
    if field == "air_date" and v:
        try:
            return date.fromisoformat(v)
        except ValueError:
            return v
    if field == "episodes" and v and str(v).isdigit():
        return int(v)
    return v


def _split_season(season):
    """'1104' → (11, 4)；'16年' → (16, None)；其他 → (None, None)。与原表补番的写法一致。"""
    if not season:
        return None, None
    if SEASON_RE.match(season):
        return int(season[:2]), int(season[2:])
    if season.endswith("年") and season[:-1].isdigit():
        return int(season[:-1]), None
    return None, None


def export_xlsx(rows, rubric, path):
    """导出成和原来相似的布局：每季一张表 + 补番 + 名作之壁 + 汇总（静态值）。"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    widths = {"名称": 40, "题材": 22, "制作公司": 18, "个人评价": 24, "开播时间": 12, "作品名": 44,
              "罗马音": 40, "日文原名": 36, "Bangumi 中文名": 30, "封面链接": 20, "简介": 30,
              "评分明细": 26, "附加信息": 24}
    fields = [f for f, _, _ in FIELDS] + [f for f, _ in BGM_COLS]
    heads_main = EXPORT_COLS + BGM_LABELS

    def write_row(ws, i, r, first_col, season_note=None):
        for j, f in enumerate(fields, first_col):
            val = _cell_value(r, f)
            if f == "_extra" and season_note:
                ex = json.loads(val) if val else {}
                ex["原季度"] = season_note
                val = json.dumps(ex, ensure_ascii=False)
            c = _safe_cell(ws, i, j, val)
            c.border = BORDER
            if f == "air_date" and isinstance(c.value, date):
                c.number_format = "yyyy/m/d"
            elif f == "bgm_id" and c.value:
                c.hyperlink = SUBJECT_URL.format(c.value)
                c.font = LINK_FONT
            elif f in ("summary", "_extra", "cover_url"):
                c.alignment = Alignment(wrap_text=False, vertical="top")
    dv_formula = '"季番,半年番,年番,剧场版,泡面番,ova"'

    by_season = defaultdict(list)
    backlog = []
    for r in rows:
        if r.get("is_backlog"):
            backlog.append(r)
            continue
        season = r.get("season")
        ok = season and (SEASON_RE.match(season) or YEAR_SHEET_RE.match(season))
        by_season[season if ok else UNKNOWN_SEASON_SHEET].append(r)

    for season in sorted(by_season, reverse=True):
        ws = wb.create_sheet(season[:31])
        _write_header(ws, heads_main, widths)
        for i, r in enumerate(sorted(by_season[season], key=lambda x: x.get("sort_order") or 0), 2):
            # 季度写法不规范（不能当表名）的放进「未知季度」，原来的写法记在附加信息里，导回时恢复
            odd = r.get("season") if season == UNKNOWN_SEASON_SHEET and r.get("season") else None
            write_row(ws, i, r, 1, season_note=odd)
        dv = DataValidation(type="list", formula1=dv_formula, allow_blank=True)
        dv.add("A2:A1000")
        ws.add_data_validation(dv)

    if backlog:
        ws = wb.create_sheet("补番")
        heads = ["年份", "季度"] + heads_main
        _write_header(ws, heads, widths)
        for i, r in enumerate(sorted(backlog, key=lambda x: x.get("sort_order") or 0), 2):
            yy, mm = _split_season(r.get("season"))
            ws.cell(i, 1, yy).border = BORDER
            ws.cell(i, 2, mm).border = BORDER
            odd = r.get("season") if r.get("season") and (yy, mm) == (None, None) else None
            write_row(ws, i, r, 3, season_note=odd)

    if rubric:
        # 和原表一样：第 1 行标题、第 2 行表头、第 3 行起是内容（导入时也按这个读）
        ws = wb.create_sheet("名作之壁")
        ws.cell(1, 1, "评分标准").font = Font(bold=True, size=13)
        ws.merge_cells("A1:D1")
        for j, h in enumerate(["分数", "等级", "代表作", "说明"], 1):
            c = ws.cell(2, j, h)
            c.font = Font(bold=True)
            c.fill = HEAD_FILL
            c.border = BORDER
        for col, w in zip("ABCD", (12, 14, 30, 50)):
            ws.column_dimensions[col].width = w
        for i, r in enumerate(rubric, 3):
            for j, k in enumerate(("score", "level", "example", "description"), 1):
                _safe_cell(ws, i, j, r.get(k)).border = BORDER

    ws = wb.create_sheet("汇总")
    heads = ["作品名", "个人评分", "季度", "体裁", "制作公司", "罗马音", "日文原名", "Bangumi ID",
             "Bangumi 评分"]
    _write_header(ws, heads, widths)
    scored = sorted((r for r in rows if r.get("score") is not None), key=lambda x: -x["score"])
    for i, r in enumerate(scored, 2):
        vals = [r["title"], r["score"], r.get("season"), r.get("format"), r.get("studio"),
                r.get("romaji"), r.get("bgm_name"), r.get("bgm_id"), r.get("bgm_score")]
        for j, v in enumerate(vals, 1):
            c = _safe_cell(ws, i, j, v)
            c.border = BORDER
            if j == 3:
                c.number_format = "@"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{max(2, len(scored) + 1)}"
    wb.save(path)
