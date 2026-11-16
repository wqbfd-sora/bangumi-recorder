"""界面配色与样式表。图标/箭头放在 bangumi_recorder/assets/，通过 Qt 的 "assets:" 搜索路径引用。"""
from pathlib import Path

from PySide6.QtCore import QDir
from PySide6.QtGui import QColor

ASSETS = Path(__file__).resolve().parent.parent / "assets"

ACCENT = "#E8707E"        # Bangumi 粉
ACCENT_DARK = "#C9505E"
ACCENT_SOFT = "#FDE8EA"
BG = "#F5F6F8"
PANEL = "#FFFFFF"
BORDER = "#E4E6EA"
TEXT = "#1F2328"
MUTED = "#6B7280"


def register_assets():
    QDir.addSearchPath("assets", str(ASSETS))


DEFAULT_PT = 11


def build_qss(pt=DEFAULT_PT):
    """按基础字号（pt）生成样式表。"""
    row_h = int(pt * 2.9)
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: {pt}pt; }}
QToolTip {{ background: #1F2328; color: white; border: none; padding: 5px 8px; border-radius: 6px; }}

/* ---------- 工具栏 ---------- */
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 10px; spacing: 6px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 4px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 8px; color: #374151; }}
QToolBar QToolButton:hover {{ background: #F1F2F4; }}
QToolBar QToolButton:pressed {{ background: #E9EBEE; }}
QToolBar QToolButton#primary {{ background: {ACCENT}; color: white; font-weight: bold; padding: 6px 14px; }}
QToolBar QToolButton#primary:hover {{ background: {ACCENT_DARK}; }}

/* 筛选栏 */
QWidget#filterBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QWidget#filterBar QComboBox {{ min-width: 90px; }}

/* 列表 / 海报 切换 */
QWidget#seg {{ background: #F1F2F4; border-radius: 9px; }}
QWidget#seg QToolButton {{ padding: 4px 12px; border-radius: 7px; color: {MUTED}; background: transparent; }}
QWidget#seg QToolButton:hover {{ color: {TEXT}; }}
QWidget#seg QToolButton:checked {{ background: white; color: {ACCENT_DARK}; font-weight: bold;
                                  border: 1px solid {BORDER}; }}

/* ---------- 左侧导航 ---------- */
QTreeWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {BORDER}; padding: 8px 6px;
                   color: #374151; }}
QTreeWidget#nav::item {{ height: {row_h}px; padding-left: 6px; border-radius: 8px; }}
QTreeWidget#nav::item:hover {{ background: #F3F4F6; }}
QTreeWidget#nav::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; font-weight: bold; }}
QTreeWidget#nav {{ show-decoration-selected: 0; selection-background-color: transparent; }}
QTreeWidget#nav::branch, QTreeWidget#nav::branch:selected, QTreeWidget#nav::branch:hover {{
    background: transparent; border: none; }}

/* ---------- 表格 ---------- */
QTableView {{ background: {PANEL}; border: none; gridline-color: transparent;
             selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QTableView#animeTable {{ padding: 0 8px; }}
QTableView::item {{ padding: 0 4px; border: none; }}
QHeaderView {{ background: {PANEL}; }}
QHeaderView::section {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER};
                       padding: 9px 10px; font-weight: 600; color: #8A9099; font-size: {pt - 1}pt; }}
QHeaderView::section:hover {{ color: {TEXT}; }}
QHeaderView::down-arrow {{ image: url(assets:sort-down.svg); width: 12px; height: 12px;
                          subcontrol-position: center right; padding-right: 4px; }}
QHeaderView::up-arrow {{ image: url(assets:sort-up.svg); width: 12px; height: 12px;
                        subcontrol-position: center right; padding-right: 4px; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

/* ---------- 海报墙 ---------- */
QListView#wall {{ background: {BG}; border: none; padding: 10px; }}

/* ---------- 右侧详情 ---------- */
QScrollArea#detail, QWidget#detailInner {{ background: {PANEL}; }}
QScrollArea#detail {{ border: none; border-left: 1px solid {BORDER}; }}
QLabel#title {{ font-size: {pt + 5}pt; font-weight: bold; }}
QLabel#subtitle {{ color: {MUTED}; }}
QLabel#romaji {{ color: #4B5563; font-style: italic; }}
QLabel#section {{ color: {MUTED}; font-weight: bold; margin-top: 8px; letter-spacing: 1px; }}
QLabel#bigScore {{ font-size: {pt + 16}pt; font-weight: bold; }}
QLabel#tier {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border-radius: 9px; padding: 2px 10px;
               font-weight: bold; }}
QLabel#cover {{ background: #F3F4F6; border-radius: 10px; color: {MUTED}; }}
QToolButton#panelClose {{ background: transparent; border: none; border-radius: 14px; color: #9CA3AF;
                         min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
                         font-size: {pt + 1}pt; padding: 0; }}
QToolButton#panelClose:hover {{ background: #F1F2F4; color: {TEXT}; }}
QToolButton#panelClose:pressed {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{ background: white; border: 1px solid {BORDER};
    border-radius: 8px; padding: 5px 8px; selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: #CDD1D6; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{ background: #F7F8F9; color: #A0A4AA; }}
QLineEdit#search {{ min-width: 120px; padding: 6px 10px; border-radius: 9px; background: #F5F6F8;
                    border-color: transparent; }}
QLineEdit#search:focus {{ background: white; border-color: {ACCENT}; }}
QLineEdit#scoreEdit {{ font-size: {pt + 3}pt; font-weight: bold; }}

QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px;
                       border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url(assets:chevron-down.svg); width: 14px; height: 14px; }}
QComboBox::down-arrow:on {{ image: url(assets:chevron-up.svg); }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
                              selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: {row_h - 4}px; padding: 0 8px; border-radius: 6px; }}

QSpinBox, QDoubleSpinBox {{ padding-right: 24px; }}
QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; width: 22px; border: none;
                                             background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(assets:chevron-up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url(assets:chevron-down.svg); width: 10px; height: 10px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 1px solid #C5C9CF;
                                                background: white; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(assets:check.svg); }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(assets:dot.svg); }}

/* ---------- 按钮 ---------- */
QPushButton {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ background: #F5F6F8; border-color: #CDD1D6; }}
QPushButton:pressed {{ background: #ECEEF1; }}
QPushButton:disabled {{ color: #A0A4AA; background: #FAFAFB; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; font-weight: bold; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton#primary:disabled {{ background: #F4B8BF; border-color: #F4B8BF; }}
QPushButton#danger {{ color: #C62828; }}
QPushButton#danger:hover {{ background: #FDECEA; border-color: #F5C2C0; }}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 16px; margin-right: 4px;
               border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT_DARK}; font-weight: bold; border-bottom: 2px solid {ACCENT}; }}
QTableWidget {{ background: {PANEL}; border: none; selection-background-color: {ACCENT_SOFT};
               selection-color: {TEXT}; gridline-color: #F0F1F3; }}

/* ---------- 列表 / 其他 ---------- */
QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}
QListWidget::item {{ padding: 4px; border-radius: 8px; }}
QListWidget::item:hover {{ background: #F7F7F8; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{ border: none; border-radius: 6px; text-align: center; background: #ECEEF1; height: 12px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 10px; border-radius: 7px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QMenu::item:disabled {{ color: #B8BCC3; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 0; height: 0; }}
QMenu::item:checked {{ font-weight: bold; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

/* ---------- 细滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D3D6DB; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #B8BCC3; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #D3D6DB; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #B8BCC3; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{
    width: 0; height: 0; border: none; background: none; image: none; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


QSS = build_qss()


def apply_appearance(app, pt, font_key=None):
    """设置全局字体、字号并重新套用样式表。font_key 见 fonts.PRESETS（None = 不改字体）。"""
    pt = max(8, min(18, int(pt)))
    if font_key is not None:
        from .fonts import make_font
        f = make_font(font_key, pt)
    else:
        f = app.font()
        f.setPointSize(pt)
    app.setFont(f)
    app.setStyleSheet(build_qss(pt))
    return pt


def score_color(score):
    """评分的颜色：取配置文件里这一档评分标准的颜色。"""
    if score is None:
        return QColor(MUTED)
    from .. import appconfig
    return QColor(appconfig.current().tier(score)[1])


def tinted(color, alpha=0.14):
    """标签底色：同色系的浅色。"""
    c = QColor(color)
    c.setAlphaF(alpha)
    return c
