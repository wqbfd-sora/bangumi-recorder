"""核心逻辑测试：python -m pytest tests  （或 python tests/test_core.py 路径/表格.xlsx）

需要一份 Excel 记录表做测试数据（文件名随意）。默认用项目根目录下的第一个 .xlsx 文件
（.xlsx 已被 .gitignore 排除，不会上传），也可以用命令行参数或环境变量 GUANFAN_XLSX 指定。
测试只读取 Excel，不会修改它。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402

from bangumi_recorder.bangumi import best_match, merge_fields, subject_to_fields  # noqa: E402
from bangumi_recorder.db import Database, season_from_date, score_tier  # noqa: E402
from bangumi_recorder.excel_io import export_xlsx, import_into, read_workbook  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
def _default_xlsx():
    found = sorted(ROOT.glob("*.xlsx"))
    return str(found[0]) if found else str(ROOT / "test_data.xlsx")


XLSX = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".xlsx") else None) \
    or os.environ.get("GUANFAN_XLSX") or _default_xlsx()


def _summary_scores(path):
    wb = openpyxl.load_workbook(path)
    ws = wb["汇总"]
    hdr = [c.value for c in ws[1]]
    i_name, i_score, i_q = hdr.index("作品名"), hdr.index("个人评分"), hdr.index("季度")
    return {(r[i_name], None if r[i_q] is None else str(r[i_q])): r[i_score]
            for r in ws.iter_rows(min_row=2, values_only=True) if r[i_name]}


def test_import_keeps_every_score():
    """汇总里的每一个分数都原样出现在数据库里。"""
    before = Path(XLSX).read_bytes()
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        import_into(db, XLSX)
        got = {(r["title"], r["season"]): r["score"] for r in db.all()}
        for key, score in _summary_scores(XLSX).items():
            assert key in got, f"缺少 {key}"
            assert got[key] == score, f"{key}: {got[key]} != {score}"
        db.close()
    assert Path(XLSX).read_bytes() == before, "导入不应修改原文件"


def test_export_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        import_into(db, XLSX)
        out = Path(d) / "out.xlsx"
        export_xlsx(db.all(), db.rubric(), out)
        rows, _, _ = read_workbook(out)
        a = sorted((r["title"], r["score"]) for r in db.all() if r["score"] is not None)
        b = sorted((r["title"], r["score"]) for r in rows if r["score"] is not None)
        assert a == b
        db.close()


def test_export_keeps_bangumi_links():
    """导出的 Excel 带着 Bangumi 关联、评分明细等，导回来一样不少。"""
    import json
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "a.db")
        import_into(db, XLSX)
        rows = db.all()
        r0, r1 = rows[0], rows[-1]
        db.update(r0["id"], {"bgm_id": 400602, "bgm_name": "葬送のフリーレン", "bgm_name_cn": "葬送的芙莉莲",
                             "bgm_score": 9.1, "bgm_rank": 1, "cover_url": "https://lain.bgm.tv/x.jpg",
                             "summary": "第一段\n第二段", "romaji": "Sousou no Frieren",
                             "extra": json.dumps({"评分明细": {"剧情": 9, "制作": 8.5, "节奏": 9},
                                                  "罗马音来源": "自动转写"}, ensure_ascii=False)})
        db.update(r1["id"], {"bgm_id": 129807, "comment": "看完\n很好"})
        out = Path(d) / "out.xlsx"
        export_xlsx(db.all(), db.rubric(), out)
        db2 = Database(Path(d) / "b.db")
        rep = import_into(db2, out)
        assert rep.linked == 2
        back = {(r["title"], r["season"]): r for r in db2.all()}
        a = back[(r0["title"], r0["season"])]
        assert (a["bgm_id"], a["bgm_score"], a["bgm_rank"], a["romaji"]) == (400602, 9.1, 1, "Sousou no Frieren")
        assert a["summary"] == "第一段\n第二段" and a["cover_url"].endswith("x.jpg")
        ex = json.loads(a["extra"])
        assert ex["评分明细"] == {"剧情": 9.0, "制作": 8.5, "节奏": 9.0} and ex["罗马音来源"] == "自动转写"
        b = back[(r1["title"], r1["season"])]
        assert b["bgm_id"] == 129807 and b["comment"] == "看完\n很好"
        assert len(db2.all()) == len(db.all()) and len(db2.rubric()) == len(db.rubric())
        db.close()
        db2.close()


def test_failed_import_keeps_old_data():
    """导入中途出错时，原来的数据必须原封不动（1.0.9 以前会被清空）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "a.db")
        import_into(db, XLSX)
        before = db.count()
        real_add, n = db.add, [0]

        def flaky_add(item, commit=True):
            n[0] += 1
            if n[0] == 30:
                raise RuntimeError("模拟导入出错")
            return real_add(item, commit)
        db.add = flaky_add
        try:
            import_into(db, XLSX, replace=True)
        except RuntimeError:
            pass
        db.add = real_add
        assert db.count() == before
        db.close()


def test_score_input_parsing():
    from bangumi_recorder.ui.detail_panel import parse_score
    assert parse_score("8.5") == (8.5, None)
    assert parse_score("8,5") == (8.5, None)
    assert parse_score("８．５") == (8.5, None)
    assert parse_score("-1") == (-1.0, None)
    assert parse_score("nan") == (None, "nan")
    assert parse_score("\\") == (None, "\\")
    assert parse_score("  ") == (None, None)


def test_export_formula_like_text_and_odd_season():
    import openpyxl
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "a.db")
        import_into(db, XLSX)
        r = [x for x in db.all() if not x["is_backlog"]][0]
        db.update(r["id"], {"comment": "=神作", "season": "25/07"})
        b = [x for x in db.all() if x["is_backlog"]][0]
        db.update(b["id"], {"season": "未知", "summary": "网页复制来的\x0b控制字符\x01"})
        out = Path(d) / "o.xlsx"
        export_xlsx(db.all(), db.rubric(), out)          # 不能因为「/」不能当表名而失败
        ws = openpyxl.load_workbook(out)["未知季度"]
        cells = [c for row in ws.iter_rows() for c in row if c.value == "=神作"]
        assert cells and cells[0].data_type == "s"        # 存成文字，不是公式
        db2 = Database(Path(d) / "b.db")
        import_into(db2, out)
        back = [x for x in db2.all() if x["title"] == r["title"]]
        assert len(back) == 1 and back[0]["season"] == "25/07" and back[0]["comment"] == "=神作"
        assert len(db2.all()) == len(db.all())            # 汇总里的同一部不会再被重复导入成补番
        bb = [x for x in db2.all() if x["title"] == b["title"] and x["is_backlog"]]
        assert len(bb) == 1 and bb[0]["season"] == "未知" and bb[0]["summary"] == "网页复制来的控制字符"
        db.close()
        db2.close()


def test_merge_never_touches_personal_fields():
    row = {"title": "我的名字", "score": 8.6, "status": "Done", "comment": "好", "studio": "",
           "tags": "我的题材", "extra": None}
    fields = {"bgm_id": 1, "title": "官方名", "score": 1, "status": "x", "comment": "y",
              "studio": "MAPPA", "tags": "官方/标签"}
    out = merge_fields(row, fields)
    assert "title" not in out and "score" not in out and "status" not in out and "comment" not in out
    assert out["studio"] == "MAPPA"
    assert "tags" not in out   # 已有内容不覆盖


def test_relink_syncs_broadcast_season():
    row = {"title": "白箱剧场版", "season": "14年", "air_date": "2014-10-09", "studio": "P.A.WORKS",
           "score": 9.5, "bgm_id": 1, "extra": None}
    fields = {"bgm_id": 2, "season": "2001", "air_date": "2020-02-29", "studio": "别的公司", "title": "x"}
    out = merge_fields(row, fields, sync_broadcast=True)
    assert out["season"] == "2001" and out["air_date"] == "2020-02-29"
    assert "studio" not in out and "title" not in out and "score" not in out
    assert "season" not in merge_fields(row, fields)          # 不同步时季度不动


def test_score_calculator():
    from decimal import Decimal
    from bangumi_recorder.scoring import DEFAULT_ITEMS, weighted_score
    f = lambda sc: weighted_score(sc, DEFAULT_ITEMS)[1]   # noqa: E731
    assert f({"剧情": 8, "制作": 7, "节奏": 9}) == Decimal("7.9")          # 正好整除，不进位
    assert f({"剧情": 8.5, "制作": 8, "节奏": 9}) == Decimal("8.5")        # 8.45 → 8.5
    assert f({"剧情": 8.3, "制作": 8.3, "节奏": 8.4}) == Decimal("8.4")    # 8.32 → 8.4
    assert f({"剧情": 9, "制作": 9, "节奏": 9}) == Decimal("9.0")
    assert f({"剧情": 8, "制作": 6}) == Decimal("7.3")                     # 缺一项：按 5:3 → 7.25 → 7.3
    assert f({}) is None


def test_quick_score_input():
    from bangumi_recorder.scoring import quick_score as q
    assert q("86") == ("ok", 8.6, True) and q("05") == ("ok", 0.5, True)
    assert q("10") == ("ok", 10.0, True) and q("100") == ("ok", 10.0, True)
    assert q("9") == ("ok", 9.0, False)                        # 还可能接着输
    assert q("8.6")[1] == 8.6 and q("8,6")[1] == 8.6 and q("８６")[1] == 8.6
    assert q("9.")[0] == "partial" and q("")[0] == "partial"
    for bad in ("101", "10.5", "8.65", "abc", "-1"):
        assert q(bad)[0] == "bad", bad


def test_helpers():
    assert season_from_date("2025-07-05") == "2507"
    assert season_from_date("2024-12-10") == "2410"
    assert season_from_date("2024-12-28") == "2501"
    assert season_from_date("2024-09-29") == "2410"
    assert score_tier(10) == "标杆" and score_tier(8.6) == "名作" and score_tier(-1) == "恶心人之作"
    subj = {"id": 5, "name": "Frieren", "name_cn": "葬送的芙莉莲", "date": "2023-09-29",
            "platform": "TV", "total_episodes": 28, "rating": {"score": 9.1, "rank": 1},
            "images": {"large": "http://x/l.jpg"}, "tags": [{"name": "奇幻", "count": 9}]}
    f = subject_to_fields(subj, [{"name": "MADHOUSE", "relation": "动画制作"}])
    assert f["season"] == "2310" and f["format"] == "半年番" and f["studio"] == "Madhouse"
    assert f["weekday"] == "金"
    assert best_match({"title": "葬送的芙莉莲", "season": "2310"}, [subj]) is subj
    assert best_match({"title": "葬送的芙莉莲", "season": "2601"}, [subj]) is None
    s2 = {"id": 9, "name": "薬屋のひとりごと 第2期", "name_cn": "药屋少女的呢喃 第二季", "date": "2025-01-10"}
    s1 = {"id": 8, "name": "薬屋のひとりごと", "name_cn": "药屋少女的呢喃", "date": "2023-10-22"}
    assert best_match({"title": "薬屋のひとりごと", "season": "2501"}, [s2, s1]) is s2   # 改过的搜索词


def test_match_rezero_season4():
    """Bangumi 把 Re:0 第四季排在第 9 个、名称也对不上（第4期 上半 / 第四季 丧失篇）。"""
    from bangumi_recorder.bangumi import rank_candidates
    data = [(140001, "Re:ゼロから始める異世界生活", "Re：从零开始的异世界生活", "2016-04-03"),
            (425998, "Re:ゼロから始める異世界生活 3rd season 襲擊編", "Re：从零开始的异世界生活 第三季 袭击篇", "2024-10-02"),
            (510728, "Re:ゼロから始める異世界生活 3rd season 反擊編", "Re：从零开始的异世界生活 第三季 反击篇", "2025-02-05"),
            (547888, "Re:ゼロから始める異世界生活 4th season 喪失編", "Re：从零开始的异世界生活 第四季 丧失篇", "2026-04-08"),
            (633836, "Re:ゼロから始める異世界生活 4th season 奪還編", "Re：从零开始的异世界生活 第四季 夺还篇", "2026-08-12"),
            (638494, "Re:ゼロから始める休憩時間 4th season", "Re：从零开始的休息时间4", "2026-04-08")]
    res = [{"id": i, "name": n, "name_cn": c, "date": d} for i, n, c, d in data]
    row = {"title": "Re:从零开始的异世界生活 第4期 上半", "season": "2604"}
    assert best_match(row, res)["id"] == 547888
    assert rank_candidates(row, res)[0]["id"] == 547888
    assert best_match({"title": "Re:从零开始的异世界生活 第4期 下半", "season": "2607"}, res)["id"] == 633836
    assert best_match({"title": "Re:0 从零开始的异世界生活3 下半 反击篇", "season": "2501"}, res)["id"] == 510728
    assert best_match({"title": "完全不相干的作品", "season": "2604"}, res) is None


def test_config_file_rubric_and_options():
    """评分标准、状态、体裁放在 data/config.json；旧数据库里的评分标准第一次打开时搬过去。"""
    import json
    from bangumi_recorder.appconfig import AppConfig, parse_range
    assert parse_range("(9,10)") == (9, False, 10, False) and parse_range("（8，9）") == (8, False, 9, False)
    assert parse_range("小于等于4")[2:] == (4, True) and parse_range("9以上")[:2] == (9, True)
    assert parse_range("8-9") == (8, True, 9, True) and parse_range("随便写") is None
    with tempfile.TemporaryDirectory() as d:
        # 旧版数据库：评分标准在 rubric 表里，还没有 config.json
        db = Database(Path(d) / "a.db")
        (Path(d) / "config.json").unlink()
        db.conn.execute("INSERT INTO rubric (score, level, example, description) VALUES ('10','满分','x','')")
        db.conn.execute("INSERT INTO rubric (score, level, example, description) VALUES ('(5,10)','还行','','')")
        db.add({"title": "t", "status": "弃坑", "format": "短片"})
        db.conn.commit()
        db.close()
        db = Database(Path(d) / "a.db")                     # 重新打开：没有 config.json → 搬过去
        cfg = db.cfg
        assert [r["level"] for r in db.rubric()] == ["满分", "还行"]
        assert "弃坑" in cfg.status_names() and "短片" in cfg.formats()
        assert cfg.tier(10)[0] == "满分" and cfg.tier(7)[0] == "还行" and cfg.tier(3)[0] == ""
        saved = json.loads((Path(d) / "config.json").read_text(encoding="utf-8"))
        assert saved["rubric"][0]["level"] == "满分"
        # 精确分数优先于区间；颜色导入 Excel 时保留
        cfg.set_rubric([{"score": "[5,10]", "level": "区间", "color": "#111111"},
                        {"score": "8", "level": "正好8", "color": "#222222"}])
        assert cfg.tier(8) == ("正好8", "#222222") and cfg.tier(8.5) == ("区间", "#111111")
        cfg.set_rubric([{"score": "8", "level": "正好8"}])
        assert cfg.tier(8)[1] == "#222222"
        db.close()
        # 配置文件被改坏：改名留底，用默认值，不影响打开
        (Path(d) / "config.json").write_text("{坏掉了", encoding="utf-8")
        bad = AppConfig(Path(d) / "config.json")
        assert bad.tier(10)[0] == "标杆" and (Path(d) / "config.broken.json").exists()


def test_current_season_and_browse():
    from datetime import date
    from bangumi_recorder.bangumi import BangumiClient, subject_region
    from bangumi_recorder.db import current_season, season_compare, season_date_range, shift_season
    assert current_season(date(2026, 11, 15)) == "2610" and current_season(date(2026, 12, 25)) == "2701"
    assert shift_season("2610", 1) == "2701" and shift_season("2601", -1) == "2510"
    assert season_compare("2610", date(2026, 11, 15)) == 0 and season_compare("2701", date(2026, 11, 15)) == 1
    assert season_compare("2607", date(2026, 11, 15)) == -1 and season_compare("16年") is None
    for sea in ("2601", "2604", "2607", "2610"):
        a, b = season_date_range(sea)
        from datetime import timedelta
        assert season_from_date(a.isoformat()) == sea and season_from_date((b - timedelta(days=1)).isoformat()) == sea
        assert season_from_date(b.isoformat()) == shift_season(sea, 1)
    # 翻页：Bangumi 每页最多 20 条
    calls = []

    class C(BangumiClient):
        def _req(self, method, path, **kw):
            calls.append((path, kw["json"]))
            off = int(path.split("offset=")[1])
            data = [{"id": i, "date": "2026-10-03"} for i in range(off, min(off + 20, 45))]
            if off == 20 and kw["json"]["sort"] == "heat":   # 热度变了：第 2 页混进第 1 页的一条，挤掉一条
                data[-1] = {"id": 0, "date": "2026-10-03"}
            return {"total": 45, "data": data}
    items = C().browse_season("2610")
    assert sorted(x["id"] for x in items) == list(range(45)) and len(calls) == 6
    assert calls[0][1]["filter"]["air_date"] == [">=2026-09-20", "<2026-12-20"] and calls[0][1]["sort"] == "heat"
    assert subject_region({"name": "おしりたんてい", "meta_tags": []}) == "日本"
    assert subject_region({"name": "x", "meta_tags": ["中国"]}) == "国产"


def test_romaji_pick():
    from bangumi_recorder.romaji import pick
    cands = [{"title": {"romaji": "MF Ghost", "native": "MFゴースト"}, "startDate": {"year": 2023, "month": 10}},
             {"title": {"romaji": "MF Ghost 2nd Season", "native": "MFゴースト 2nd Season"},
              "startDate": {"year": 2024, "month": 10}}]
    assert pick("MFゴースト", "2023-10-01", cands)["title"]["romaji"] == "MF Ghost"
    assert pick("MFゴースト 2nd Season", "2024-10-06", cands)["title"]["romaji"] == "MF Ghost 2nd Season"
    assert pick("MFゴースト 3rd Season", "2026-01-01", cands) is None
    sl = [{"title": {"romaji": "Shangri-La Frontier 2nd Season", "native": "シャングリラ・フロンティア 2nd season"},
           "startDate": {"year": 2024, "month": 10}}]
    assert pick("シャングリラ・フロンティア～クソゲーハンター、神ゲーに挑まんとす～ 2nd Season", "2024-10-13", sl)


def test_romaji_from_bangumi_aliases():
    from bangumi_recorder.romaji import from_aliases
    assert from_aliases("葬送のフリーレン", ["Frieren: Beyond Journey's End", "Sousou no Frieren"]) == \
        "Sousou no Frieren"
    assert from_aliases("響け！ユーフォニアム3", ["Hibike! Euphonium 3", "Sound! Euphonium 3"]) == \
        "Hibike! Euphonium 3"
    assert from_aliases("シュタインズ・ゲート ゼロ", ["STEINS;GATE 0", "石头门 0"]) is None   # 交给 AniList
    assert from_aliases("LAZARUS", []) == "LAZARUS"


def test_new_title_fields_match_sheet_style():
    from bangumi_recorder.bangumi import resolve_studio, to_simplified
    assert to_simplified("小川優樹") == "小川优树"
    assert to_simplified("斎藤圭一郎") == "斋藤圭一郎"
    known = {"passione": "Passione", "whitefox": "WHITE FOX", "京都动画": "京都动画"}
    assert resolve_studio(["パッショーネ"], ["赌博", "Passione"], known) == "Passione"
    assert resolve_studio(["WHITE FOX"], [], known) == "WHITE FOX"
    assert resolve_studio(["京都アニメーション"], [], known) == "京都动画"
    assert resolve_studio(["Studio五組"], [], {}) == "Studio五组"
    subj = {"id": 1, "name": "FX戦士くるみちゃん", "name_cn": "FX战士久留美", "date": "2026-10-01",
            "platform": "TV", "eps": 12, "total_episodes": 14, "meta_tags": ["TV", "日本", "漫画改", "萌系"],
            "tags": [{"name": "2026年10月", "count": 9}, {"name": "赌博", "count": 8},
                     {"name": "Passione", "count": 7}, {"name": "投资", "count": 6}],
            "infobox": [{"key": "放送星期", "value": "星期四"}]}
    people = [{"name": "パッショーネ", "relation": "动画制作"}, {"name": "小川優樹", "relation": "导演"},
              {"name": "猪原健太", "relation": "系列构成"}]
    f = subject_to_fields(subj, people, known)
    assert (f["studio"], f["director"], f["series_comp"]) == ("Passione", "小川优树", "猪原健太")
    assert (f["episodes"], f["format"], f["season"], f["weekday"]) == ("12", "季番", "2610", "木")
    assert f["tags"] == "萌系/赌博/投资"


def test_old_database_gets_romaji_column():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "old.db"
        from bangumi_recorder.db import SCHEMA
        old_schema = "\n".join(line for line in SCHEMA.splitlines() if "romaji" not in line)
        c = sqlite3.connect(path)
        c.executescript(old_schema)
        c.execute("INSERT INTO anime (title, created_at, updated_at) VALUES ('x', 'a', 'a')")
        c.commit()
        c.close()
        db = Database(path)
        db.update(1, {"romaji": "Ekkusu"})
        assert db.get(1)["romaji"] == "Ekkusu"
        db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        XLSX = sys.argv[1]
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("✓", name)
    print("全部通过")
