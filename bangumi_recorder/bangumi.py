"""Bangumi (bgm.tv) API v0 客户端。文档：https://bangumi.github.io/api/

只读接口不需要登录。Bangumi 要求请求带一个能识别应用的 User-Agent。
"""
import json
import re
import threading
import time
from pathlib import Path
from datetime import date as _date

import requests

from .net import SessionPool

from . import __version__
from .db import extra_of, season_date_range, season_from_date
from .romaji import (SOURCE_ANILIST, SOURCE_BANGUMI, SOURCE_KAKASI, from_aliases, lookup_one,
                     subject_aliases)

API = "https://api.bgm.tv"
SUBJECT_URL = "https://bgm.tv/subject/{}"
USER_AGENT = f"wqbfd-sora/bangumi-recorder/{__version__} (personal desktop app)"
ANIME = 2  # SubjectType：2 = 动画

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


class BangumiError(Exception):
    pass


class BangumiClient:
    def __init__(self, token=None, timeout=15):
        self.token = token
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.pool = SessionPool(headers)     # 借用-归还的连接池（见 net.py 的说明）
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()

    def _req(self, method, path, **kw):
        # 简单限速，避免短时间内请求太密
        with self._lock:
            wait = 0.35 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            with self.pool.session() as sess:
                r = sess.request(method, API + path, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise BangumiError(f"网络错误：{e}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise BangumiError(f"Bangumi 返回 {r.status_code}：{r.text[:200]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError as e:          # 偶尔会返回网页（维护/限流页面）而不是数据
            raise BangumiError(f"Bangumi 返回的不是有效数据（HTTP {r.status_code}），可能正在维护，稍后再试") from e

    # ---------- 查询 ----------
    def search(self, keyword, limit=20, season=None):
        """按名字搜索动画。season（如 '2610'）：只要这一季开播的。
        Bangumi 一次最多返回 20 条，要更多时自动翻页。"""
        items, _total = self.search_page(keyword, limit=min(limit, 20), season=season)
        offset = len(items)
        while len(items) < limit and offset < _total:
            more, _total = self.search_page(keyword, limit=min(20, limit - len(items)), offset=offset,
                                            season=season)
            if not more:
                break
            items += more
            offset += len(more)
        return items

    def search_page(self, keyword, limit=20, offset=0, season=None, sort=None):
        """一页搜索结果：(条目列表, 总数)。关键词为空时按热度列出（配合 season 就是「这一季的新番」）。"""
        flt = {"type": [ANIME]}
        if season:
            start, end = season_date_range(season)
            flt["air_date"] = [f">={start.isoformat()}", f"<{end.isoformat()}"]
        body = {"keyword": keyword or "", "sort": sort or ("match" if keyword else "heat"), "filter": flt}
        data = self._req("POST", f"/v0/search/subjects?limit={int(limit)}&offset={int(offset)}", json=body) or {}
        return data.get("data", []) or [], int(data.get("total") or 0)

    def browse_season(self, season, progress=None, cancelled=None, max_items=600):
        """把一整季开播的动画都拉下来（按热度）。progress(已拿到, 总数)。"""
        seen, out = set(), []
        total = 0
        # 按热度翻页时，热度在变，可能一条出现两次、另一条被挤掉；少了就换个排序再补一遍
        for sort in ("heat", "rank"):
            offset, total = 0, None
            while total is None or offset < min(total, max_items):
                if cancelled and cancelled():
                    return out
                page, total = self.search_page("", limit=20, offset=offset, season=season, sort=sort)
                if not page:
                    break
                offset += len(page)
                for it in page:
                    if it.get("id") in seen:
                        continue
                    seen.add(it.get("id"))
                    if season_from_date(it.get("date")) in (season, None):
                        out.append(it)
                if progress:
                    progress(min(len(seen), total), min(total, max_items))
            if len(seen) >= min(total or 0, max_items):
                break
        return out

    def subject(self, subject_id):
        return self._req("GET", f"/v0/subjects/{int(subject_id)}")

    def persons(self, subject_id):
        return self._req("GET", f"/v0/subjects/{int(subject_id)}/persons") or []

    def download(self, url):
        try:
            with self.pool.session() as sess:
                r = sess.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            raise BangumiError(f"下载失败：{e}") from e

    # ---------- 组合 ----------
    def details(self, subject_id, known_studios=None, with_persons=True):
        """作品详情 + 制作人员，整理成本地字段。known_studios：{规范化键: 你表里的写法}。
        with_persons=False 时不查制作人员（快一倍，但拿不到制作公司/监督/系构）。"""
        subj = self.subject(subject_id)
        if subj is None:
            raise BangumiError(f"找不到条目 {subject_id}")
        people = []
        if with_persons:
            try:
                people = self.persons(subject_id)
            except BangumiError:
                people = []
        return subject_to_fields(subj, people, known_studios)


# ---------- 字段转换 ----------
def _infobox_all(subj, *keys):
    out = []
    for item in subj.get("infobox") or []:
        if item.get("key") in keys:
            v = item.get("value")
            vals = [x.get("v") for x in v if isinstance(x, dict)] if isinstance(v, list) else [v]
            out.extend(str(x).strip() for x in vals if x)
    return out


def _infobox(subj, *keys):
    vals = _infobox_all(subj, *keys)
    return "/".join(vals) if vals else None


def _people_list(people, *relations):
    names = []
    for p in people or []:
        if p.get("relation") in relations and p.get("name") and p["name"] not in names:
            names.append(p["name"])
    return names


# 日文新字体补充对照（OpenCC 表里没有的常见字）
_JP_EXTRA = str.maketrans({
    "斎": "斋", "沢": "泽", "広": "广", "辺": "边", "竜": "龙", "浜": "滨", "恵": "惠", "栄": "荣",
    "桜": "樱", "亜": "亚", "円": "圆", "実": "实", "伝": "传", "単": "单", "発": "发", "県": "县",
    "渋": "涩", "黒": "黑", "徳": "德", "豊": "丰", "斉": "齐", "剣": "剑", "歩": "步", "経": "经",
    "総": "总", "絵": "绘", "関": "关", "鉄": "铁", "対": "对", "帰": "归", "気": "气", "様": "样",
    "薫": "薰", "蔵": "藏", "嶋": "岛", "嶌": "岛", "峯": "峰", "髙": "高", "﨑": "崎", "隆": "隆",
    "瀬": "濑", "滝": "泷", "姫": "姬", "芸": "艺", "読": "读", "売": "卖", "楽": "乐", "労": "劳",
    "／": "/", "、": "/", "・": "·",
})


_T2S = None


def _t2s_table():
    """逐字对照表（由 OpenCC 的数据生成，见 assets/t2s_NOTICE.txt）。"""
    global _T2S
    if _T2S is None:
        path = Path(__file__).resolve().parent / "assets" / "t2s.json"
        try:
            _T2S = str.maketrans(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _T2S = {}
    return _T2S


def to_simplified(text):
    """人名里的繁体/日文汉字转成简体（假名不动），和原表的写法保持一致。"""
    if not text:
        return text
    return text.translate(_JP_EXTRA).translate(_t2s_table()).strip()


def norm_key(s):
    return re.sub(r"[\s.\-_·・,，、/／:：!！]", "", str(s or "")).lower()


# 制作公司的常见别名（日文/英文/简称 → 原表里的写法）。你表里已有的写法优先。
_STUDIO_ALIASES = {
    "京都动画": ["京都アニメーション", "京阿尼", "京都動画", "KyoAni", "Kyoto Animation"],
    "动画工房": ["動画工房", "Doga Kobo"],
    "Studio五组": ["Studio五組", "スタジオ五組", "Studio Gokumi", "五组"],
    "WHITE FOX": ["ホワイトフォックス", "白狐社"],
    "Passione": ["パッショーネ"],
    "J.C.STAFF": ["ジェー・シー・スタッフ", "JC"],
    "Sunrise": ["サンライズ", "日升"],
    "东宝动画": ["TOHO animation", "東宝アニメーション"],
    "东映动画": ["東映アニメーション", "Toei Animation"],
    "TMS娱乐": ["トムス・エンタテインメント", "TMS Entertainment"],
    "旭Production": ["旭プロダクション", "Asahi Production"],
    "Tsumugi": ["Tsumugi Akita Animation Lab", "つむぎ秋田アニメLab"],
    "Bibury动画工作室": ["Bibury Animation Studios", "ビブリーアニメーションスタジオ"],
    "SILVER LINK.": ["シルバーリンク", "SILVER LINK"],
    "Seven Arcs": ["セブン・アークス"],
    "MAPPA": ["マッパ"],
    "Madhouse": ["MADHOUSE", "マッドハウス"],
    "CygamesPictures": ["Cygames Pictures"],
    "BN Pictures": ["バンダイナムコピクチャーズ"],
    "Studio MOTHER": ["スタジオマザー"],
    "SynergySP": ["シナジーSP"],
}
_ALIAS_INDEX = {norm_key(a): canon for canon, al in _STUDIO_ALIASES.items() for a in [canon, *al]}


def resolve_studio(candidates, tags, known=None):
    """制作公司：优先用你表里已有的写法，其次用常见别名，再看 Bangumi 标签里有没有同一家公司的写法，
    最后才用 Bangumi 的原文（转成简体）。"""
    known = known or {}
    cands = [c for c in candidates if c]
    if not cands:
        return None
    parts = []
    for c in cands[:2] if len(cands) > 1 and "/" not in cands[0] else cands[:1]:
        k = norm_key(c)
        if k in known:
            parts.append(known[k])
            continue
        canon = _ALIAS_INDEX.get(k)
        if canon:
            parts.append(known.get(norm_key(canon), canon))
            continue
        simp = to_simplified(c)
        if norm_key(simp) in known:
            parts.append(known[norm_key(simp)])
            continue
        parts.append(simp)
    # 只有一家公司、且原文是日文假名时：如果标签里有一个你表里认识的公司名，就用那个
    if len(parts) == 1 and re.search(r"[\u3040-\u30ff]", parts[0]):
        for t in tags:
            k = norm_key(t)
            if k in known:
                return known[k]
    return "/".join(dict.fromkeys(parts))


def guess_format(subj, eps=None):
    platform = (subj.get("platform") or "").upper()
    eps = eps or subj.get("eps") or subj.get("total_episodes") or 0
    if "剧场" in platform or platform == "MOVIE":
        return "剧场版"
    if platform in ("OVA", "OAD"):
        return "ova"
    if platform == "WEB" and eps and eps >= 1 and _short_episodes(subj):
        return "泡面番"
    if eps >= 40:
        return "年番"
    if eps >= 20:
        return "半年番"
    return "季番"


def subject_region(subj):
    """地区：日本 / 国产 / 欧美 / 韩国 / 其他（看 Bangumi 的公共标签）。"""
    meta = set(subj.get("meta_tags") or [])
    for key, label in (("日本", "日本"), ("中国", "国产"), ("欧美", "欧美"), ("美国", "欧美"), ("韩国", "韩国")):
        if key in meta:
            return label
    # 新条目常常还没有公共标签：看用户标签和原名的文字
    tags = set(subject_tag_names(subj))
    name = subj.get("name") or ""
    if tags & {"欧美", "美国", "法国", "英国", "美漫", "美画"}:
        return "欧美"
    if "韩国" in tags or re.search(r"[\uac00-\ud7a3]", name):
        return "韩国"
    if tags & {"国产", "国创", "中国"}:
        return "国产"
    if re.search(r"[\u3040-\u30ff]", name):
        return "日本"
    return "其他"


def subject_tag_names(subj):
    """公共标签 + 用户标签（去重，公共标签在前）。"""
    names = list(subj.get("meta_tags") or [])
    for t in subj.get("tags") or []:
        n = t.get("name") if isinstance(t, dict) else t
        if n and n not in names:
            names.append(n)
    return names


def is_adult(subj):
    return bool(subj.get("nsfw")) or "里番" in subject_tag_names(subj)


def _short_episodes(subj):
    t = _infobox(subj, "片长") or ""
    m = re.search(r"(\d+)", t)
    return bool(m and int(m.group(1)) <= 8)


def weekday_of(date_str):
    try:
        y, m, d = (int(x) for x in date_str[:10].split("-"))
        return WEEKDAY_JP[_date(y, m, d).weekday()]
    except (ValueError, AttributeError, TypeError):
        return None


_CN_WEEKDAY = {"一": "月", "二": "火", "三": "水", "四": "木", "五": "金", "六": "土", "日": "日", "天": "日"}


def _weekday(subj, date):
    w = _infobox(subj, "放送星期") or ""
    m = re.search(r"(?:星期|周|週)([一二三四五六日天])", w)
    if m:
        return _CN_WEEKDAY[m.group(1)]
    return weekday_of(date)


def _date_from_infobox(subj):
    v = _infobox(subj, "放送开始", "上映年度", "发售日", "开始")
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", v or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


_TAG_SKIP = {"TV", "日本", "动画", "日本动画", "原创", "漫画改", "漫改", "轻小说改", "轻改", "小说改",
             "游戏改", "GAL改", "Gal改", "WEB", "剧场版", "OVA", "OAD", "青年向", "少年向", "少女向",
             "TV动画", "续作", "第二季", "第三季", "未确定", "2D", "3D"}


def clean_tags(subj, exclude=(), n=4):
    """题材：先用 Bangumi 的官方分类（meta_tags），再补用户标签；去掉年份、改编来源、公司名、人名、作品名。"""
    title_keys = {norm_key(subj.get("name")), norm_key(subj.get("name_cn"))}
    bad = {norm_key(x) for x in exclude if x} | {norm_key(x) for x in _TAG_SKIP}

    def ok(name):
        if not name:
            return False
        k = norm_key(name)
        if not k or k in bad or re.fullmatch(r"\d{4}(年\d{1,2}月)?|\d{4}年", name):
            return False
        if any(k and k in t for t in title_keys):      # 作品名/系列名
            return False
        return True

    out = []
    for name in subj.get("meta_tags") or []:
        if ok(name) and name not in out:
            out.append(name)
    for t in sorted(subj.get("tags") or [], key=lambda t: -(t.get("count") or 0))[:20]:
        name = t.get("name")
        if len(out) >= n:
            break
        if ok(name) and name not in out:
            out.append(name)
    return "/".join(out[:n]) or None


def subject_to_fields(subj, people=None, known_studios=None):
    """把 Bangumi 条目转成本地 anime 表的字段（只包含能拿到的）。"""
    date = subj.get("date") or _date_from_infobox(subj)
    rating = subj.get("rating") or {}
    images = subj.get("images") or {}
    box_eps = _infobox(subj, "话数")
    eps = subj.get("eps") or (int(box_eps) if box_eps and box_eps.isdigit() else None) \
        or subj.get("total_episodes")
    studio_cands = _people_list(people, "动画制作") or _infobox_all(subj, "动画制作")
    tag_names = [t.get("name") for t in subj.get("tags") or [] if t.get("name")]
    directors = _people_list(people, "导演") or _infobox_all(subj, "导演")
    comps = _people_list(people, "系列构成") or _infobox_all(subj, "系列构成")
    studio = resolve_studio(studio_cands, tag_names, known_studios)
    person_names = [to_simplified(x) for x in directors + comps] + directors + comps
    return {
        "bgm_id": subj.get("id"),
        "bgm_name": subj.get("name") or None,
        "bgm_name_cn": subj.get("name_cn") or None,
        "bgm_score": rating.get("score") or None,
        "bgm_rank": rating.get("rank") or None,
        "cover_url": images.get("large") or images.get("common") or images.get("medium") or None,
        "summary": subj.get("summary") or None,
        # 以下是可以用来补全个人表格的信息
        "title": subj.get("name_cn") or subj.get("name"),
        "air_date": date,
        "season": season_from_date(date),
        "weekday": _weekday(subj, date),
        "episodes": str(eps) if eps else None,
        "format": guess_format(subj, eps),
        "studio": studio,
        "director": to_simplified("/".join(directors)) or None,
        "series_comp": to_simplified("/".join(comps)) or None,
        "tags": clean_tags(subj, exclude=studio_cands + [studio] + person_names),
        "aliases": subject_aliases(subj),     # 不存库，只用来找罗马音
    }


BGM_FIELDS = ["bgm_id", "bgm_name", "bgm_name_cn", "bgm_score", "bgm_rank", "cover_url", "summary"]
FILLABLE = ["season", "air_date", "weekday", "episodes", "format", "studio", "director", "series_comp",
            "tags"]


BROADCAST = ["season", "air_date"]      # 播出季度、开播时间


def merge_fields(row, fields, fill_only_blank=True, sync_broadcast=False):
    """决定要写回数据库的字段。

    - Bangumi 自己的字段（bgm_*、封面、简介）总是更新；
    - sync_broadcast=True（重新关联、刷新时）：播出季度和开播时间以 Bangumi 为准；
    - 其他个人表格字段默认只填空白格（fill_only_blank=False 时以 Bangumi 为准）；
    - 绝不碰评分、状态、个人评价、作品名。
    """
    out = {k: fields.get(k) for k in BGM_FIELDS
           if fields.get(k) is not None and fields.get(k) != row.get(k)}
    extra = extra_of(row)
    extra_changed = False
    uncertain_date = "开播时间原值" in extra
    for k in FILLABLE:
        v = fields.get(k)
        if v is None:
            continue
        if (not fill_only_blank or row.get(k) in (None, "") or (k == "air_date" and uncertain_date)
                or (sync_broadcast and k in BROADCAST)):
            if v != row.get(k):
                out[k] = v
    if "air_date" in out and uncertain_date:      # 开播时间已换成 Bangumi 的准确日期
        extra.pop("开播时间原值", None)
        extra_changed = True
    # 罗马音：空着、或之前只是自动转写而这次查到了 AniList / Bangumi 的写法 → 填入
    rj, src = fields.get("romaji"), fields.get("romaji_source")
    was_guess = extra.get("罗马音来源") == SOURCE_KAKASI
    better = src in (SOURCE_ANILIST, SOURCE_BANGUMI)
    if rj and (not row.get("romaji") or (was_guess and better) or not fill_only_blank):
        out["romaji"] = rj
        if src == SOURCE_KAKASI:
            extra["罗马音来源"] = SOURCE_KAKASI
        else:
            extra.pop("罗马音来源", None)
        extra_changed = True
    if extra_changed:
        out["extra"] = json.dumps(extra, ensure_ascii=False) if extra else None
    return out


def with_romaji_source(row, source):
    """在 extra 里记下罗马音是不是自动转写的（AniList 查到的就不用记）。"""
    extra = extra_of(row)
    if source == SOURCE_KAKASI:
        extra["罗马音来源"] = SOURCE_KAKASI
    else:
        extra.pop("罗马音来源", None)
    return json.dumps(extra, ensure_ascii=False) if extra else None


def full_details(client, subject_id, anilist=None, known_studios=None):
    """Bangumi 详情 + 罗马音。罗马音的来源顺序：Bangumi 别名 → AniList → 自动转写。
    罗马音查不到不影响其他信息。"""
    fields = client.details(subject_id, known_studios)
    rj = from_aliases(fields.get("bgm_name"), fields.get("aliases"))
    src = SOURCE_BANGUMI if rj else None
    if not rj:
        try:
            rj, src = lookup_one(fields.get("bgm_name"), fields.get("air_date"), anilist)
        except Exception:  # noqa: BLE001
            rj, src = None, None
    fields["romaji"], fields["romaji_source"] = rj, src
    return fields


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORD = {"1st": 1, "2nd": 2, "3rd": 3}


def _season_no(m):
    v = m.group(1)
    if v.isdigit():
        return v
    if v.lower() in _ORD:
        return str(_ORD[v.lower()])
    if v.lower().endswith("th") and v[:-2].isdigit():
        return v[:-2]
    return str(_CN_NUM.get(v, v))


def norm_title(s):
    """比较用的名称：小写、去标点空格，「第四季 / 第4期 / 4th season / season 4」都写成 4。"""
    s = (s or "").lower().replace("ｒｅ", "re")
    s = re.sub(r"第([一二三四五六七八九十]|\d+)[季期部]", lambda m: _season_no(m), s)
    s = re.sub(r"(\d+(?:st|nd|rd|th))\s*(?:season|シーズン|クール)", lambda m: _season_no(m), s)
    s = re.sub(r"(?:season|シーズン)\s*(\d+)", lambda m: m.group(1), s)
    return re.sub(r"[\s\-_·・:：,，、!！?？。.'\"“”《》【】()（）~～/]", "", s)


def title_similarity(a, b):
    from difflib import SequenceMatcher
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _sim(row_title, subj):
    return max(title_similarity(row_title, subj.get("name_cn")), title_similarity(row_title, subj.get("name")))


def rank_candidates(row, results):
    """把搜索结果按「季度对得上 → 名称最像」排在前面（Bangumi 的排序有时把要找的排在第 9 个）。"""
    season = row.get("season")
    title = row.get("title")

    def key(item):
        i, r = item
        same = bool(season) and season_from_date(r.get("date")) == season
        return (not same, -round(_sim(title, r), 3), i)
    return [r for _, r in sorted(enumerate(results), key=key)]


def best_match(row, results):
    """在搜索结果里找可以放心自动采用的条目：
    1. 名称完全一致，且季度一致（若已知）；
    2. 否则：季度一致、名称包含搜索词的候选恰好只有一个（例如搜「薬屋のひとりごと」找第 2 期）；
    3. 否则：季度一致的候选里，名称最像的那个明显比其他的像（例如「第4期 上半」对「第四季 丧失篇」）。"""
    t = norm_title(row.get("title"))
    season = row.get("season")
    known_season = bool(season and len(season) == 4 and season.isdigit())
    hits = []
    for r in results:
        names = {norm_title(r.get("name_cn")), norm_title(r.get("name"))}
        if t and t in names:
            rs = season_from_date(r.get("date"))
            if known_season and rs and rs != season:
                continue
            hits.append(r)
    if len(hits) == 1:
        return hits[0]
    if hits or not known_season or not t:
        return None
    same = [r for r in results if season_from_date(r.get("date")) == season]
    near = [r for r in same if any(t in n for n in (norm_title(r.get("name_cn")), norm_title(r.get("name"))) if n)]
    if len(near) == 1:
        return near[0]
    if near:
        return None
    scored = sorted(((_sim(row.get("title"), r), r) for r in same), key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.7 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1]
    return None
