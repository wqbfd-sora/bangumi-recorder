# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置。用法：双击 build.bat（或 python -m PyInstaller "Bangumi Recorder.spec"）
import os
import re

from PyInstaller.utils.hooks import collect_data_files

# 版本号取自 bangumi_recorder/__init__.py，输出文件夹会叫「Bangumi Recorder 1.0.1」这样
with open(os.path.join(SPECPATH, "bangumi_recorder", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

datas = [("bangumi_recorder/assets", "bangumi_recorder/assets"),
         ("LICENSE", ".")]                  # GPL：发布 exe 时要附上许可证
datas += collect_data_files("pykakasi")          # 罗马音转写用的字典

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "PySide6.QtNetwork", "pykakasi"],   # QtSvg：样式表里的 svg 箭头需要
    hookspath=[],
    runtime_hooks=[],
    # Anaconda 自带很多大包；这里用不到，排除掉可以让打包快很多、体积小很多。
    # PyQt5/PyQt6 必须排除，否则 PyInstaller 会因为同时发现两套 Qt 而报错。
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "numpy", "pandas", "matplotlib", "scipy",
              "IPython", "jupyter", "notebook", "PIL", "lxml", "sqlalchemy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bangumi Recorder",
    console=False,
    icon="bangumi_recorder/assets/icon.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=f"Bangumi Recorder {VERSION}", upx=False)
