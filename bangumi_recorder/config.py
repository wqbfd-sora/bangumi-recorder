"""路径与常量。数据默认放在项目目录下的 data/ 文件夹，方便备份和随项目移动。"""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)      # 是否是 PyInstaller 打包后的 exe
if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent


def _frozen_data_dir():
    """exe 旁边的 data_path.txt 写着数据文件夹的位置（build.bat 会自动生成），
    这样 exe 和 `python run.py` 用的是同一个数据库。没有这个文件就用 exe 旁边的 data。"""
    hint = ROOT / "data_path.txt"
    if hint.exists():
        raw = hint.read_bytes()
        for enc in ("utf-8-sig", "gbk", "mbcs"):
            try:
                text = raw.decode(enc).strip().strip('"')
            except (UnicodeDecodeError, LookupError):
                continue
            if text:
                return Path(text)
    # 没有 data_path.txt：exe 一般在「项目/dist/某版本/」里，往上找项目里已有的数据库
    for parent in ROOT.parents[:3]:
        if (parent / "data" / "anime.db").exists():
            return parent / "data"
    return ROOT / "data"


if os.environ.get("BANGUMI_RECORDER_DATA"):
    DATA_DIR = Path(os.environ["BANGUMI_RECORDER_DATA"])
elif FROZEN:
    DATA_DIR = _frozen_data_dir()
else:
    DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "anime.db"
COVER_DIR = DATA_DIR / "covers"
BACKUP_DIR = DATA_DIR / "backups"

# 与 Excel 里一致的选项
FORMATS = ["季番", "半年番", "年番", "剧场版", "泡面番", "ova"]
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
STATUSES = ["Done", "ing", "DL", "补番"]

SEASON_MONTHS = {"01": "1月", "04": "4月", "07": "7月", "10": "10月"}


def ensure_dirs():
    for d in (DATA_DIR, COVER_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
