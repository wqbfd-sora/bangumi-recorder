"""「当季」「未开播」的显示：按电脑的本机日期判断。"""
from PySide6.QtGui import QColor

from ..db import current_season, season_compare, season_label, shift_season

CURRENT_COLOR = QColor("#E11D48")      # 当季：醒目的红
FUTURE_COLOR = QColor("#2563EB")       # 定档还没开播的：蓝


def season_tag(season):
    """'当季' / '未开播' / ''。"""
    c = season_compare(season)
    if c == 0:
        return "当季"
    if c == 1:
        return "未开播"
    return ""


def season_choices(past=12, future=2):
    """下拉框用：从「未来 future 季」到「过去 past 季」，新的在前。[(季度, 显示文字)]。"""
    cur = current_season()
    out = []
    for n in range(future, -past - 1, -1):
        s = shift_season(cur, n)
        tag = season_tag(s)
        out.append((s, season_label(s) + (f" · {tag}" if tag else "")))
    return out
