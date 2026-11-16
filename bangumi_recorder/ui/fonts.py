"""界面字体。程序自带几款免费可商用的开源字体（SIL Open Font License 1.1，见 assets/fonts/OFL-*.txt）：

- 思源黑体 Noto Sans SC：简体中文（也包含日文假名和英文）
- 思源黑体 Noto Sans JP：日文字形
- Inter：英文、数字

中文、日文、英文混排时按顺序找字：前面的字体里没有的字，自动用后面的字体显示。
"""
import logging

from PySide6.QtGui import QFont, QFontDatabase

from .style import ASSETS

log = logging.getLogger(__name__)
FONT_DIR = ASSETS / "fonts"
BUNDLED_FILES = ["NotoSansSC-Regular.ttf", "NotoSansSC-Bold.ttf", "NotoSansJP-Regular.ttf",
                 "NotoSansJP-Bold.ttf", "Inter-Regular.ttf", "Inter-Bold.ttf"]
SYSTEM_CJK = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
              "Source Han Sans SC", "Yu Gothic UI", "Meiryo UI"]

# (设置里存的键, 显示的名字, 字体顺序)
PRESETS = [
    ("system", "系统默认（微软雅黑）", []),
    ("noto_sc", "思源黑体 SC（简体中文）", ["Noto Sans SC", "Noto Sans JP"]),
    ("noto_jp", "思源黑体 JP（日文字形）", ["Noto Sans JP", "Noto Sans SC"]),
    ("inter", "Inter + 思源黑体（英文数字更精致）", ["Inter", "Noto Sans SC", "Noto Sans JP"]),
]
DEFAULT_KEY = "system"
_loaded = None


def load_bundled():
    """注册程序自带的字体（只做一次）。坏了 / 缺了的文件跳过，不影响启动。"""
    global _loaded
    if _loaded is not None:
        return _loaded
    _loaded = set()
    for name in BUNDLED_FILES:
        path = FONT_DIR / name
        if not path.exists():
            log.warning("缺少字体文件 %s", name)
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid < 0:
            log.warning("字体文件读不了：%s", name)
            continue
        _loaded.update(QFontDatabase.applicationFontFamilies(fid))
    return _loaded


def _system_fallback():
    have = set(QFontDatabase.families())
    return [f for f in SYSTEM_CJK if f in have]


def families_for(key):
    """设置里的字体选项 → 字体顺序（最后都接上系统里的中文字体兜底）。"""
    load_bundled()
    have = set(QFontDatabase.families())
    if key and key.startswith("custom:"):
        chosen = [key[len("custom:"):]]
    else:
        chosen = next((fams for k, _, fams in PRESETS if k == key), [])
    out = [f for f in chosen if f in have]
    for f in _system_fallback():
        if f not in out:
            out.append(f)
    return out


def make_font(key, pt):
    f = QFont()
    fams = families_for(key)
    if fams:
        f.setFamilies(fams)
    f.setPointSize(int(pt))
    return f


def label_for(key):
    if key and key.startswith("custom:"):
        return key[len("custom:"):]
    return next((label for k, label, _ in PRESETS if k == key), PRESETS[0][1])
