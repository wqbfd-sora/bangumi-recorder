"""日文原名 → 罗马音。

0. 先看 Bangumi 条目的「别名」：里面常有罗马音（如 Sousou no Frieren），但和英文译名混在一起，
   所以拿日文读音的自动转写去比对，足够像的才采用。
1. 再查 AniList（https://anilist.co，公开的 GraphQL 接口，不用登录）。AniList 的 romaji 标题
   是按官方/通行写法整理的，例如「薬屋のひとりごと」→ "Kusuriya no Hitorigoto"。
2. AniList 查不到时，用 pykakasi 把日文按读音转写（汉字读音可能不准，会标记出来）。
"""
import re
import threading
import time
from difflib import SequenceMatcher

from .net import SessionPool

from . import __version__

ANILIST = "https://graphql.anilist.co"
SOURCE_BANGUMI = "Bangumi"
SOURCE_ANILIST = "AniList"
SOURCE_KAKASI = "自动转写"

_FIELDS = "id title { romaji native } startDate { year month day } format"


def _norm(s):
    s = (s or "").lower()
    s = s.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
                                  "0123456789abcdefghijklmnopqrstuvwxyz"))
    return re.sub(r"[\s\-_·・:：,，、!！?？。.'\"“”「」『』《》【】()（）~～/〜♪☆★]", "", s)


def _ym(date_str):
    m = re.match(r"(\d{4})-(\d{2})", date_str or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def pick(native, air_date, candidates):
    """在 AniList 搜索结果里挑出对应的条目；拿不准就返回 None。"""
    want = _norm(native)
    ym = _ym(air_date)

    def same_month(c):
        sd = c.get("startDate") or {}
        if not ym or not sd.get("year"):
            return None
        y, m = sd["year"], sd.get("month") or 0
        # 允许差一个月（季末开播、先行放送等）
        return abs((y * 12 + m) - (ym[0] * 12 + ym[1])) <= 1

    exact = [c for c in candidates if _norm((c.get("title") or {}).get("native")) == want]
    for c in exact:
        if same_month(c) in (True, None):
            return c
    if exact and not ym and len(exact) == 1:
        return exact[0]
    # 原名写法略有出入（如多了副标题），但开头一致、开播月份也一致
    for c in candidates:
        nat = _norm((c.get("title") or {}).get("native"))
        if not nat or not want or not same_month(c):
            continue
        prefix = 0
        for a, b in zip(nat, want):
            if a != b:
                break
            prefix += 1
        if nat in want or want in nat or prefix >= min(6, len(nat), len(want)):
            return c
    return None


class AniListClient:
    def __init__(self, timeout=20):
        self.pool = SessionPool({"User-Agent": f"wqbfd-sora/bangumi-recorder/{__version__}",
                                 "Content-Type": "application/json", "Accept": "application/json"})
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0

    def _post(self, query, variables):
        for attempt in range(3):
            with self._lock:
                wait = 2.1 - (time.monotonic() - self._last)   # AniList 限速约 30 次/分钟
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            with self.pool.session() as sess:
                r = sess.post(ANILIST, json={"query": query, "variables": variables}, timeout=self.timeout)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "30")) + 1)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("errors") and not data.get("data"):
                raise RuntimeError(str(data["errors"])[:300])
            return data.get("data") or {}
        raise RuntimeError("AniList 请求太频繁，请稍后再试")

    def search_many(self, natives):
        """一次请求里查多个原名（GraphQL 别名），返回与输入同序的候选列表。"""
        if not natives:
            return []
        decl = ", ".join(f"$s{i}: String" for i in range(len(natives)))
        body = " ".join(f"a{i}: Page(perPage: 6) {{ media(search: $s{i}, type: ANIME) {{ {_FIELDS} }} }}"
                        for i in range(len(natives)))
        data = self._post(f"query ({decl}) {{ {body} }}", {f"s{i}": n for i, n in enumerate(natives)})
        return [((data.get(f"a{i}") or {}).get("media") or []) for i in range(len(natives))]


# ---------- pykakasi 转写 ----------
_kks = None


def kakasi_romaji(text):
    """用 pykakasi 转写；没装 pykakasi 时返回 None。"""
    global _kks
    if not text:
        return None
    try:
        if _kks is None:
            import pykakasi
            _kks = pykakasi.kakasi()
    except ImportError:
        return None
    punct = {"【": "[", "】": "]", "「": "\"", "」": "\"", "『": "\"", "』": "\"", "、": ",", "。": ".",
             "！": "!", "？": "?", "・": " ", "～": " ~ ", "〜": " ~ ", "（": "(", "）": ")", "：": ":"}
    particles = {"は": "wa", "へ": "e", "を": "wo"}
    parts = []
    for item in _kks.convert(text):
        orig, hep = item.get("orig", ""), item.get("hepburn", "")
        if re.fullmatch(r"[\x00-\x7f]+", orig):      # 本来就是英文/数字，原样保留
            parts.append(orig.strip())
        elif orig in particles:
            parts.append(particles[orig])
        elif all(ch in punct or ch.isspace() for ch in orig):
            parts.append("".join(punct.get(ch, " ") for ch in orig))
        elif hep:
            parts.append(hep.strip())
    out = " ".join(p for p in parts if p).replace("・", " ")
    out = re.sub(r"\s+([,.!?:;)\]])", r"\1", out)
    out = re.sub(r"([(\[])\s+", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:1].upper() + out[1:] if out else None


def lookup_many(items, anilist=None, batch=8, report=None, cancelled=None):
    """items: [(key, 日文原名, 开播日期)] → ({key: (罗马音, 来源)}, [查询失败的 key])。

    AniList 有回应但没有对应条目 → 用 pykakasi 转写；AniList 请求失败 → 记为失败，不转写
    （之后可以重试）。report(done, total) 用来更新进度；cancelled() 为 True 时提前结束。
    """
    anilist = anilist or AniListClient()
    out, failed = {}, []
    todo = [it for it in items if it[1]]
    for start in range(0, len(todo), batch):
        if cancelled and cancelled():
            break
        chunk = todo[start:start + batch]
        try:
            results = anilist.search_many([n for _, n, _ in chunk])
        except Exception:  # noqa: BLE001
            failed.extend(k for k, _, _ in chunk)
            results = None
        if results is not None:
            for (key, native, air), cands in zip(chunk, results):
                hit = pick(native, air, cands)
                if hit and (hit.get("title") or {}).get("romaji"):
                    out[key] = (hit["title"]["romaji"], SOURCE_ANILIST)
                else:
                    rk = kakasi_romaji(native)
                    if rk:
                        out[key] = (rk, SOURCE_KAKASI)
        if report:
            report(min(start + batch, len(todo)), len(todo))
    return out, failed


def lookup_one(native, air_date, anilist=None):
    """返回 (罗马音, 来源)；查不到或网络失败时为 (None, None)。"""
    res, _ = lookup_many([(0, native, air_date)], anilist)
    return res.get(0, (None, None))


# ---------- Bangumi 别名 ----------
def _cond(s):
    """比对用：小写、去符号、合并长音（furiiren ≈ frieren，shuugeki ≈ shugeki）。"""
    s = (s or "").lower()
    for a, b in (("ō", "o"), ("ū", "u"), ("ā", "a"), ("ē", "e"), ("ī", "i"), ("ⅱ", "ii"), ("ⅲ", "iii")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]", "", s)
    for a, b in (("ou", "o"), ("uu", "u"), ("oo", "o"), ("ii", "i"), ("ee", "e"), ("aa", "a")):
        s = s.replace(a, b)
    return s


def _mostly_latin(s):
    s = (s or "").strip()
    return bool(s) and bool(re.search(r"[A-Za-z]", s)) and \
        sum(1 for ch in s if ch.isascii() or ch in "āēīōūĀĒĪŌŪ") / len(s) >= 0.9


def from_aliases(native, aliases, threshold=0.6):
    """在 Bangumi 别名里找罗马音：和日文读音转写最像、且相似度够高的那个。找不到返回 None。"""
    if _mostly_latin(native):
        return native.strip()            # 原名本来就是英文字母，如 LAZARUS
    reading = kakasi_romaji(native)
    if not reading:
        return None
    target = _cond(reading)
    best, best_score = None, 0.0
    for alias in aliases or []:
        if not _mostly_latin(alias):
            continue
        score = SequenceMatcher(None, _cond(alias), target).ratio()
        if score > best_score:
            best, best_score = alias.strip(), score
    return best if best_score >= threshold else None


def subject_aliases(subj):
    """从 Bangumi 条目的 infobox 里取出所有别名。"""
    out = []
    for item in (subj or {}).get("infobox") or []:
        if item.get("key") != "别名":
            continue
        v = item.get("value")
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(str(x.get("v")) for x in v if isinstance(x, dict) and x.get("v"))
    return out
