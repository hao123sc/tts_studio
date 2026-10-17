"""PyInstaller 打包脚本：把 TTS Studio 打成 Windows 免安装目录（zip 即发）。

用法（在仓库根目录）::

    pip install pyinstaller
    python build/build_exe.py

产物：``dist/TTSStudio/`` 目录（含 TTSStudio.exe 与依赖），把整个目录压缩成 zip
分发即可，用户双击 TTSStudio.exe 运行，无需命令行。

说明：

* 采用 **onedir** 模式：PySide6 应用 onefile 启动慢、杀毒误报率高，onedir 更稳；
* 如需单文件，把 ``--onedir`` 改为 ``--onefile``（启动会慢 5~20 秒）；
* ``示例数据/`` 会一并打进包里，方便用户点「导入CSV」直接试用。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """执行打包。"""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name", "TTSStudio",
        "--paths", str(ROOT),
        # 隐藏导入（运行期动态 import 的模块）
        "--hidden-import", "edge_tts",
        "--hidden-import", "pyttsx3",
        "--hidden-import", "pyttsx3.drivers",
        "--hidden-import", "pyttsx3.drivers.sapi5",
        "--hidden-import", "lameenc",
        "--hidden-import", "scipy.signal",
        # 打包示例数据
        "--add-data", f"{ROOT / '示例数据'}{';' if sys.platform.startswith('win') else ':'}示例数据",
        # 图标（如需自备图标，放 build/icon.ico 并去掉注释）
        # "--icon", str(ROOT / "build" / "icon.ico"),
        str(ROOT / "app" / "main.py"),
    ]
    print("执行：", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc == 0:
        out = ROOT / "dist" / "TTSStudio"
        print(f"\n打包完成：{out}")
        print("把整个 TTSStudio 目录压缩为 zip 分发；用户双击 TTSStudio.exe 即可运行。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
