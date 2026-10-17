"""TTS Studio 配音工作站 — 程序入口。

用法：

* ``python -m app.main`` 或 ``python app/main.py``：启动图形界面；
* ``python app.main --screenshot docs/界面截图.png``：离屏渲染主窗口截图（打包自测用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许 "python app/main.py" 直接运行：把仓库根加入 sys.path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _make_app(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("TTS Studio 配音工作站")
    app.setOrganizationName("TTSStudio")
    # 教师用户、1366×768 笔记本也要看清：基础字号 11pt
    font = QFont()
    font.setPointSize(11)
    families = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                "Noto Sans CJK SC", "SimHei", "WenQuanYi Micro Hei", "sans-serif"]
    font.setFamilies([f for f in families if f.strip()])
    app.setFont(font)
    return app


def run_screenshot(out_path: str) -> int:
    """离屏渲染主窗口并截图（docs/界面截图.png 生成用）。"""
    import json

    from PySide6.QtCore import QTimer

    from app.core.project import Segment
    from app.ui.main_window import MainWindow

    app = _make_app(sys.argv)
    win = MainWindow()
    win.resize(1366, 800)
    # 填入示例项目，便于截图有真实内容
    demo_segments = [
        Segment(i=1, name="从隋唐走来的产业", text="同学们好，今天我们来讲一枚蚕茧的低碳之旅。[pause=1.5]早在隋唐时期，我国的丝绸业就已十分兴盛。", status="ok", duration=12.3),
        Segment(i=2, name="蚕的一生", text="一条蚕从蚁蚕到结茧，大约要吃下二十千克桑叶。", status="ok", duration=8.1),
        Segment(i=3, name="学生提问", text="老师，缫丝是不是就是把蚕茧抽成丝呀？", status="ok", duration=6.4),
        Segment(i=4, name="绿色低碳", text="每生产一千克生丝，大约排放 2.0千克二氧化碳，我们来看如何减排。", status="failed", error="网络错误示例"),
        Segment(i=5, name="标准解读", text="按照 GB/T 24067 标准，LCA 全生命周期评价要覆盖原料、生产、运输。", status="pending"),
    ]
    win.project.project = "一枚蚕茧的低碳之旅（演示）"
    win.project.default_voice = "edge:zh-CN-YunxiNeural"
    win.project.segments = demo_segments
    win._voice_names = {"edge:zh-CN-YunxiNeural": "云希（少年男声）", "edge:zh-CN-XiaoxiaoNeural": "晓晓（温柔女声）"}
    win.segment_table.set_segments(win.project.segments, win._voice_names)
    win.param_panel.set_params({"rate": -0.05, "sample_rate": 44100, "bitrate": 128})
    win.queue_panel.append("✓ 第 1 段完成 → 01.mp3（12.3s）")
    win.queue_panel.append("✓ 第 2 段完成 → 02.mp3（8.1s）")
    win.queue_panel.append("✗ 第 4 段失败：网络错误示例")
    win.statusBar().showMessage("演示数据 — 实际使用请导入《蚕茧课程41段.csv》")
    win.show()

    def snap() -> None:
        pix = win.grab()
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        pix.save(out_path)
        print(f"截图已保存：{out_path}")
        app.quit()

    QTimer.singleShot(1200, snap)
    return app.exec()


def main() -> int:
    """程序入口。"""
    args = sys.argv[1:]
    if args and args[0] == "--screenshot":
        return run_screenshot(args[1] if len(args) > 1 else "docs/界面截图.png")

    from app.ui.main_window import MainWindow

    app = _make_app(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
