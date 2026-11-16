"""界面回归测试（不弹窗口，离屏运行）：python tests/test_gui.py [测试用的表格.xlsx]

覆盖曾经导致闪退的操作：
- 改了季度没保存 → 直接点「重新关联」→ 选「保存」→ 弹出搜索框（1.0.3 在这里闪退）
- 搜索框里连续重新搜索，旧的缩略图回来时列表已经清空
用一份临时数据库，不会碰你的真实数据。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="bangumi_gui_test_")
os.environ["BANGUMI_RECORDER_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

XLSX = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "GUANFAN_XLSX", str(next(iter(sorted(Path(__file__).resolve().parent.parent.glob("*.xlsx"))),
                             Path(__file__).resolve().parent.parent / "test_data.xlsx")))


def fake_season(season, n=30):
    """假的一季新番：TV / WEB / 剧场版 / 其他，日本 / 国产，带一部 R18。"""
    from bangumi_recorder.db import season_date_range
    start, _end = season_date_range(season)
    out = []
    for i in range(n):
        plat = ["TV", "TV", "WEB", "剧场版", "其他"][i % 5]
        region = "中国" if i % 7 == 3 else "日本"
        out.append({"id": 900000 + int(season) * 100 + i, "name": f"テスト{season}-{i}",
                    "name_cn": f"测试新番{season}-{i}", "date": (start.replace(day=28)).isoformat(),
                    "platform": plat, "eps": 12 if plat == "TV" else 1, "total_episodes": 12 if plat == "TV" else 1,
                    "meta_tags": [plat, region, "恋爱" if i % 2 else "战斗"], "tags": [{"name": "漫画改"}],
                    "rating": {"score": 6 + i % 4}, "nsfw": i == 9, "images": {"grid": f"http://x/{i}.png"}})
    return out


class FakeBangumi:
    def search(self, kw, limit=20, season=None):
        time.sleep(0.02)
        if season:
            return [x for x in fake_season(season) if kw in x["name_cn"]][:limit]
        return [{"id": i, "name": f"{kw}{i}", "name_cn": f"{kw}{i}", "date": "2020-01-01",
                 "images": {"grid": f"http://x/{i}.png"}} for i in range(10)]

    def browse_season(self, season, progress=None, cancelled=None, max_items=600):
        items = fake_season(season)
        for i in range(0, len(items), 10):
            time.sleep(0.01)
            if progress:
                progress(min(i + 10, len(items)), len(items))
        return items

    def details(self, sid, known=None, with_persons=True):
        from bangumi_recorder.bangumi import subject_to_fields
        subj = next(x for s in ("2610", "2701", "2607") for x in fake_season(s) if x["id"] == sid)
        f = subject_to_fields(subj, [{"name": "MAPPA", "relation": "动画制作"}] if with_persons else [], known)
        f["aliases"] = []
        return f

    def download(self, url):
        time.sleep(0.2)
        raise RuntimeError("测试里不下载")


def pump(app, n=30):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication(sys.argv)
    from bangumi_recorder.db import Database
    from bangumi_recorder.excel_io import import_into
    from bangumi_recorder.ui import main_window as mw
    from bangumi_recorder.ui import search_dialog as sd

    db = Database()
    import_into(db, XLSX)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Save)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    def fake_exec(dialog):
        dialog.search()                      # 再搜一次：旧结果的缩略图回来时列表已清空
        pump(app, 40)
        dialog.selected = None
        return 0
    sd.SearchDialog.exec = fake_exec

    win = mw.MainWindow(db)
    win._client = FakeBangumi()
    win.show()
    for layout in ("table", "wall"):
        win.set_layout(layout)
        for view in (("backlog", None), ("season", "2501")):
            win.view = view
            win.reload()
            for _ in range(2):
                r = win.model.rows[0]
                win.select_id(r["id"])
                pump(app)
                win.detail.w["season"].setText("")
                win.detail._mark_dirty()
                win.link_row(r["id"])
                pump(app)
                win.table.viewport().repaint()
                win.wall.viewport().repaint()
    print("✓ 改季度后直接重新关联：没有闪退")

    # 详情面板开着的时候后台更新了这一行，用户再保存，不能把后台的更新冲掉
    win.set_layout("table")
    win.view = ("season", "2507")
    win.reload()
    rid = win.model.rows[0]["id"]
    win.select_id(rid)
    pump(app)
    db.update(rid, {"romaji": "BACKGROUND ROMAJI", "studio": "BG Studio"})
    win.reload(rid)
    win.detail.comment.setPlainText("我写的评价")
    win.detail._mark_dirty()
    win.detail.save()
    pump(app)
    r = db.get(rid)
    assert r["romaji"] == "BACKGROUND ROMAJI" and r["studio"] == "BG Studio" and r["comment"] == "我写的评价", r
    print("✓ 保存时不会覆盖后台刚更新的字段")

    # 改了没保存 → 直接点另一行 → 选「保存」：被保存的这一行因为排序/筛选换了位置或被筛掉（1.0.11 在这里闪退）
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win.set_layout("table")
    win.view = ("all", None)
    win.reload()
    for i in range(12):
        ids = [win.proxy.index(j, 0).data(mw.ID_ROLE) for j in range(win.proxy.rowCount())]
        a = ids[(i * 5) % len(ids)]
        win.select_id(a)
        pump(app, 3)
        if i % 3 == 0:                                   # 筛选：改名后不再匹配搜索词
            win.search.setText(win.detail.row["title"][:1])
            win.detail.w["title"].setText(f"改名后的作品{i}")
            expect = ("title", f"改名后的作品{i}")
        elif i % 3 == 1:                                 # 总表按季度排序：改季度换位置
            win.detail.w["season"].setText("16年")
            expect = ("season", "16年")
        else:
            win.detail.comment.setPlainText(f"评价{i}")
            expect = ("comment", f"评价{i}")
        win.detail._mark_dirty()
        ids = [win.proxy.index(j, 0).data(mw.ID_ROLE) for j in range(win.proxy.rowCount())]
        others = [j for j, x in enumerate(ids) if x != a]
        target_row = others[len(others) // 2]
        target = ids[target_row]
        rect = win.table.visualRect(win.proxy.index(target_row, 1))
        win.table.scrollTo(win.proxy.index(target_row, 1))
        rect = win.table.visualRect(win.proxy.index(target_row, 1))
        QTest.mouseClick(win.table.viewport(), Qt.LeftButton, pos=rect.center())
        pump(app, 5)
        win.table.viewport().repaint()
        assert db.get(a)[expect[0]] == expect[1], (expect, db.get(a)[expect[0]])
        assert win.detail.row["id"] == target and not win.detail.is_dirty()
        win.search.setText("")
        pump(app, 2)
    print("✓ 改了没保存直接点别的行 → 保存：不闪退，保存成功并跳到点的那一行")

    # 评分计算器：86 = 8.6，输完跳下一项；删字不跳；项目重名不能填入
    from bangumi_recorder.ui.score_calc import ScoreCalculator
    dlg = ScoreCalculator(db, "测试")
    dlg.show()
    pump(app, 3)
    for text in ("86", "75", "9"):
        QTest.keyClicks(app.focusWidget(), text)
        pump(app, 2)
    assert [r["score"].value() for r in dlg.rows] == [8.6, 7.5, 9.0] and dlg.big.text() == "8.4"
    sb = dlg.rows[0]["score"]
    sb.setFocus()
    pump(app, 2)
    sb.lineEdit().end(False)
    QTest.keyClick(sb.lineEdit(), Qt.Key_Backspace)
    pump(app, 2)
    assert app.focusWidget() in (sb, sb.lineEdit())
    dlg.rows[1]["name"].setText("剧情")
    assert not dlg.btn_ok.isEnabled()
    dlg.reject()
    print("✓ 评分计算器输入")

    # 滚动侧边栏时鼠标划过下拉框，不能改掉星期/体裁/状态（1.0.13 之前会）
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    win.set_layout("table")
    win.view = ("all", None)
    win.reload()
    rid = win.proxy.index(0, 0).data(mw.ID_ROLE)
    win.select_id(rid)
    pump(app, 3)
    before = {k: win.detail.w[k].currentText() for k in ("status", "format", "weekday")}
    for k in before:
        c = win.detail.w[k]
        for delta in (120, -120, -120, -120):
            ev = QWheelEvent(QPointF(5, 5), QPointF(c.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, delta),
                             Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            app.sendEvent(c, ev)
    after = {k: win.detail.w[k].currentText() for k in before}
    assert before == after and not win.detail.is_dirty(), (before, after)
    print("✓ 滚轮划过下拉框不会改值")

    # 侧边栏：回车保存并关闭；点一行又打开；✕ 关闭
    w = win.detail.w["episodes"]
    w.setFocus()
    w.setText("99")
    win.detail._mark_dirty()
    QTest.keyClick(w, Qt.Key_Return)
    pump(app, 3)
    assert db.get(rid)["episodes"] == "99" and win.detail.isHidden()
    rect = win.table.visualRect(win.proxy.index(1, 1))
    QTest.mouseClick(win.table.viewport(), Qt.LeftButton, pos=rect.center())
    pump(app, 3)
    assert not win.detail.isHidden()
    QTest.mouseClick(win.detail.btn_close, Qt.LeftButton)
    pump(app, 3)
    assert win.detail.isHidden()
    print("✓ 回车保存并关闭侧边栏、点行重新打开、✕ 关闭")

    # 状态格：按住 → 拖到选项 → 松开
    win.view = ("all", None)
    win.reload()
    col = win.table.col_keys.index("status")
    idx = win.proxy.index(2, col)
    rid = idx.data(mw.ID_ROLE)
    QTest.mousePress(win.table.viewport(), Qt.LeftButton, pos=win.table.visualRect(idx).center())
    pump(app, 3)
    menu = win.table._menu
    target = next(a for a in menu.actions() if a.text() and a.text() != (db.get(rid)["status"] or "") and a.text() != "清空")
    p = menu.actionGeometry(target).center()
    QTest.mouseMove(menu, p)
    QTest.mouseRelease(menu, Qt.LeftButton, pos=p)
    pump(app, 3)
    assert db.get(rid)["status"] == target.text()
    print("✓ 状态格按住拖选")

    # 海报墙：三列/四列临界宽度上不能来回闪
    win.set_layout("wall")
    win.view = ("all", None)
    win.reload()
    for width in range(520, 900, 7):
        win.wall.resize(width, 600)
        seen = []
        for _ in range(12):
            app.processEvents()
            seen.append((win.wall.gridSize().width(), win.wall.verticalScrollBar().isVisible()))
        flips = sum(1 for a, b in zip(seen, seen[1:]) if a != b)
        assert flips <= 2, (width, seen)
    print("✓ 海报墙改变宽度不会闪")

    # 评分标准与选项：状态改名（表里一起改）、改评分档位 → 表格评级跟着变
    from bangumi_recorder.ui.dialogs import OptionsDialog
    from bangumi_recorder.db import score_tier
    n_ing = db.conn.execute("SELECT COUNT(*) FROM anime WHERE status = 'ing'").fetchone()[0]
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    dlg = OptionsDialog(db, win)
    for i in range(dlg.st.rowCount()):
        if dlg.st.item(i, 0).text() == "ing":
            dlg.st.item(i, 0).setText("在看")
    dlg.rt.item(0, 1).setText("满分神作")
    dlg._save()
    win.apply_options()
    assert db.conn.execute("SELECT COUNT(*) FROM anime WHERE status = '在看'").fetchone()[0] == n_ing
    assert "在看" in [win.f_status.itemText(i) for i in range(win.f_status.count())]
    assert score_tier(10) == "满分神作"
    print("✓ 评分标准与选项：改名、档位生效")

    # 字体：每个选项都能切换，不出错
    from bangumi_recorder.ui import fonts
    fonts.load_bundled()
    for key, _label, _f in fonts.PRESETS:
        win.apply_font_size(12, key)
        pump(app, 2)
        assert QApplication.instance().font().families()
    win.apply_font_size(11, "custom:不存在的字体")
    print("✓ 切换字体")

    # 体裁格也能按住拖选
    win.set_layout("table")
    win.view = ("all", None)
    win.reload()
    col = win.table.col_keys.index("format")
    idx = win.proxy.index(3, col)
    rid = idx.data(mw.ID_ROLE)
    QTest.mousePress(win.table.viewport(), Qt.LeftButton, pos=win.table.visualRect(idx).center())
    pump(app, 3)
    menu = win.table._menu
    target = next(a for a in menu.actions() if a.text() and a.text() not in ((db.get(rid)["format"] or ""), "清空"))
    p = menu.actionGeometry(target).center()
    QTest.mouseMove(menu, p)
    QTest.mouseRelease(menu, Qt.LeftButton, pos=p)
    pump(app, 3)
    assert db.get(rid)["format"] == target.text()
    print("✓ 体裁格按住拖选")

    # 当季：按本机日期在左边标红；没有作品也列出来
    from bangumi_recorder.db import current_season, shift_season
    from bangumi_recorder.ui.season_util import CURRENT_COLOR
    cur = current_season()
    found = []
    stack = [win.nav.topLevelItem(i) for i in range(win.nav.topLevelItemCount())]
    while stack:
        it = stack.pop()
        key = it.data(0, Qt.UserRole)
        if key and tuple(key) == ("season", cur):
            found.append(it)
        stack.extend(it.child(i) for i in range(it.childCount()))
    assert found and "●" in found[0].text(0) and found[0].foreground(0).color() == CURRENT_COLOR
    print("✓ 当季标红")

    # 添加作品：按季度搜（不填名字就是列出这一季）、按体裁和题材筛
    dlg = sd.SearchDialog(win._client, mode="add", parent=win)
    dlg.f_season.setCurrentIndex(dlg.f_season.findData(cur))
    pump(app, 20)
    assert dlg.list.count() == 30, dlg.list.count()
    dlg.f_format.setCurrentIndex(dlg.f_format.findData("剧场版"))
    visible = [dlg.list.item(i) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert visible and all("剧场版" in (x.data(Qt.UserRole)["platform"]) for x in visible)
    dlg.f_format.setCurrentIndex(0)
    dlg.f_tag.setText("恋爱")
    visible = [dlg.list.item(i) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert len(visible) == 15
    dlg.reject()
    print("✓ 添加作品：季度 / 体裁 / 题材筛选")

    # 季度新番：拉一整季 → 筛选 → 勾选 → 批量加进季度表；再拉一次时显示「已在表里」
    from bangumi_recorder.ui.season_dialog import SeasonDialog, C_CHECK
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    future = shift_season(cur, 1)
    for target in (cur, future):
        before = len(db.all())
        sdlg = SeasonDialog(db, win._client, {}, season=target, parent=win)
        for _ in range(300):                     # 线程池里可能还排着别的任务（缩略图）
            pump(app, 2)
            if sdlg.task is None:
                break
        assert sdlg.table.rowCount() == 30, sdlg.table.rowCount()
        shown = [r for r in range(30) if not sdlg.table.isRowHidden(r)]
        # 默认：日本、不含「其他」平台、隐藏 R18
        assert all(sdlg._subject(r)["platform"] != "其他" and not sdlg._subject(r)["nsfw"] for r in shown)
        assert all("中国" not in sdlg._subject(r)["meta_tags"] for r in shown)
        sdlg._check_visible(True)
        n = len(sdlg.checked_subjects())
        assert n == len(shown) and n > 0
        sdlg.add_checked()
        for _ in range(100):
            pump(app, 2)
            if sdlg.task is None:
                break
        assert len(db.all()) == before + n, (len(db.all()), before, n)
        assert all(sdlg.table.item(r, C_CHECK).flags() & Qt.ItemIsUserCheckable == Qt.NoItemFlags
                   for r in shown)                          # 刚加的都显示「已在表里」
        sdlg.add_checked()                                  # 再点一次：什么都不会重复添加
        pump(app, 10)
        assert len(db.all()) == before + n
        sdlg.reject()
    win.reload()
    print("✓ 季度新番：拉取、筛选、批量添加、不重复（当季和未开播的季度）")

    # 第一次启动：弹窗让用户选 Excel；不选就从空白开始，以后不再弹
    from PySide6.QtWidgets import QFileDialog
    with tempfile.TemporaryDirectory() as d2:
        for choose in (False, True):
            db2 = Database(Path(d2) / f"first_{choose}.db")
            asked = []

            def fake_box_exec(box, choose=choose):
                asked.append(box.text())
                if choose:
                    box.buttons()[0].click() if box.buttons()[0].text().startswith("选择") else \
                        next(b for b in box.buttons() if b.text().startswith("选择")).click()
                return 0
            QMessageBox.exec = fake_box_exec
            QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (XLSX, ""))
            mw.TextDialog.exec = lambda self: 0               # 导入报告窗口
            w2 = mw.MainWindow(db2)
            pump(app, 40)
            assert asked and "观番史" not in asked[0]
            assert db2.get_setting("first_run_done") == "1"
            assert (db2.count() > 0) == choose
            if choose:
                assert db2.get_setting("last_import_dir") == str(Path(XLSX).parent)
            w2.close()
            asked.clear()
            w3 = mw.MainWindow(db2)                       # 再打开：不会再问
            pump(app, 40)
            assert not asked
            w3.close()
            db2.close()
    print("✓ 第一次启动：选择导入 / 从空白开始")
    win.close()
    db.close()
    print("全部通过")


if __name__ == "__main__":
    main()
