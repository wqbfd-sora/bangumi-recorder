"""双击 run.bat 或运行 `python run.py` 启动 Bangumi Recorder。"""
import sys

from bangumi_recorder.app import main

if __name__ == "__main__":
    sys.exit(main())
