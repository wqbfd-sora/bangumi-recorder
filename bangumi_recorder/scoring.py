"""评分计算：按比例加权，结果向上取整到一位小数。"""
from decimal import ROUND_CEILING, Decimal

DEFAULT_ITEMS = [
    {"name": "剧情", "weight": 5},
    {"name": "制作", "weight": 3},
    {"name": "节奏", "weight": 2},
]


def weighted_score(scores, items):
    """scores: {项目名: 分数}；items: [{"name", "weight"}]。

    返回 (未取整的加权平均, 向上取整到一位小数的结果)，都是 Decimal。缺分的项目不参与计算。
    用 Decimal 计算，避免 8.3 这类小数在二进制里多出一点点而被多进一位。
    """
    total_w = Decimal(0)
    total = Decimal(0)
    for it in items:
        name = it["name"]
        if scores.get(name) is None:
            continue
        w = Decimal(str(it.get("weight") or 0))
        if w <= 0:
            continue
        total_w += w
        total += Decimal(str(scores[name])) * w
    if total_w == 0:
        return None, None
    raw = total / total_w
    return raw, raw.quantize(Decimal("0.1"), rounding=ROUND_CEILING)


def ratio_text(items):
    return " : ".join(f"{_num(it['weight'])}" for it in items if (it.get("weight") or 0) > 0)


def _num(x):
    d = Decimal(str(x))
    return str(int(d)) if d == d.to_integral_value() else f"{d.normalize()}"


_QUICK_TRANS = str.maketrans("０１２３４５６７８９．。，,", "0123456789....")


def quick_score(text):
    """计算器里的分数输入：不打小数点也能输一位小数。

    返回 (状态, 分数, 是否已输完)，状态为 "ok" / "partial"（还在输入中）/ "bad"。
      86 → 8.6   9 → 9（可能还要接着输）   05 → 0.5   10 / 100 → 10
      8.6 / 8,6 / ８６ 也可以；超过 10 或多于一位小数的不接受。
    """
    t = (text or "").strip().translate(_QUICK_TRANS)
    if not t:
        return "partial", None, False
    if t.isdigit():
        if len(t) == 1:
            return "ok", float(t), False
        if t in ("10", "100"):
            return "ok", 10.0, True
        if len(t) == 2:
            return "ok", int(t) / 10, True
        return "bad", None, False
    head, dot, tail = t.partition(".")
    if dot and head.isdigit() and len(head) <= 2 and (tail == "" or (tail.isdigit() and len(tail) == 1)):
        val = float(f"{head}.{tail or 0}")
        if val > 10:
            return "bad", None, False
        if not tail:
            return "partial", val, False
        return "ok", val, True
    return "bad", None, False
