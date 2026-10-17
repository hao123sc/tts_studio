"""主窗口：左边挑"配音的人"，中间贴台词，右边调参数，点一下生成，批量导出。"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppConfig, load_config
from ..core.delivery import export_delivery_zip
from ..core.log import get_logger, short
from ..core.naming import render_filename
from ..core.project import (
    STATUS_FAILED,
    STATUS_OK,
    Project,
    Segment,
    load_project,
    save_project,
)
from ..core.queue import JobQueue, build_params
from ..core.textproc import count_chars, estimate_duration, split_sentences
from ..engines import get_engine
from ..engines.base import Voice
from ..core.registry import VoiceRegistry
from .param_panel import BUILTIN_PRESETS, ParamPanel
from .player import PlayerBar
from .queue_panel import QueuePanel
from .segment_table import SegmentTable
from .settings_dialog import SettingsDialog
from .voice_panel import VoicePanel
from .workers import GenerateWorker, PreviewWorker, VoiceLoadWorker

log = get_logger()


class MainWindow(QMainWindow):
    """TTS Studio 主窗。"""

    def __init__(self, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.cfg = cfg or load_config()
        self.registry = VoiceRegistry()
        self.project = Project(project="未命名项目")
        self.project_path: Path | None = None
        self.user_presets: dict[str, dict] = {}
        self._voice_names: dict[str, str] = {}
        self._voices: list[Voice] = []
        self._workers: list = []  # 防止线程被回收
        self._generate_worker: GenerateWorker | None = None
        self._dirty = False

        self.setWindowTitle("TTS Studio 配音工作站（P1 网络数字配音）")
        self.setMinimumSize(1180, 720)
        self.resize(1366, 800)
        self._build_ui()
        self._build_toolbar()
        self._connect()
        self._load_user_presets()
        self._load_engines_and_voices()
        self._restore_session()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(max(10, self.cfg.autosave_sec) * 1000)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start()
        self.statusBar().showMessage("就绪 — 左边选音色，中间贴台词，右边调参数，点「生成全部」")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        self.splitter = QSplitter(Qt.Horizontal)
        self.voice_panel = VoicePanel()
        self.segment_table = SegmentTable()
        self.param_panel = ParamPanel()
        self.splitter.addWidget(self.voice_panel)
        self.splitter.addWidget(self.segment_table)
        self.splitter.addWidget(self.param_panel)
        self.splitter.setSizes([280, 620, 320])
        root.addWidget(self.splitter, 4)

        bottom = QSplitter(Qt.Vertical)
        self.player = PlayerBar()
        self.queue_panel = QueuePanel()
        bottom.addWidget(self.player)
        bottom.addWidget(self.queue_panel)
        bottom.setSizes([60, 180])
        root.addWidget(bottom, 1)

        # 段音色工具行（FR-V-5 多音色角色）
        row = QHBoxLayout()
        row.addWidget(QLabel("选中段音色："))
        self.segment_voice = QComboBox()
        self.segment_voice.setMinimumWidth(220)
        self.btn_apply_voice = QPushButton("应用到勾选段")
        row.addWidget(self.segment_voice)
        row.addWidget(self.btn_apply_voice)
        row.addStretch(1)
        root.addLayout(row)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize() * 1.2)
        self.addToolBar(tb)

        def act(text: str, slot, tip: str = "") -> QAction:
            a = QAction(text, self)
            if tip:
                a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        act("📂 打开项目", self.open_project, "打开 .dvproj 项目文件")
        act("💾 保存", self.save_project, "保存项目（Ctrl+S）")
        act("📥 导入CSV", self.import_csv, "导入 分段 CSV（序号,文件名,文本,…）")
        act("📥 导入TXT", self.import_txt, "导入 TXT / Markdown（空行分段）")
        tb.addSeparator()
        self.act_gen_all = act("🎙 生成全部", self.generate_all, "批量生成全部段落（断点续传：未变化的自动跳过）")
        self.act_gen_sel = act("▶ 生成勾选", self.generate_selected, "只生成勾选的段落")
        self.act_redo = act("🔁 只重做这一段", self.redo_current, "忽略缓存，强制重做当前段")
        self.act_retry = act("⛑ 重试失败", self.retry_failed, "只重新生成失败的段落")
        self.act_cancel = act("⏹ 取消", self.cancel_generation, "取消生成（不留半截文件）")
        self.act_cancel.setEnabled(False)
        tb.addSeparator()
        act("📦 导出交付包", self.export_delivery, "打包 音频+清单.csv+参数.json+README.txt 为 zip")
        act("📖 发音词典", self.open_lexicon, "多音字/专名替换与数字读法规则")
        act("⚙ 设置", self.open_settings, "代理 / 缓存 / API Key / 日志")

        # 保存快捷键
        save_action = QAction(self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.save_project)
        self.addAction(save_action)

    def _connect(self) -> None:
        self.voice_panel.voice_selected.connect(self._on_voice_selected)
        self.voice_panel.preview_requested.connect(self._on_preview)
        self.voice_panel.favorite_toggled.connect(self._on_favorite)
        self.segment_table.selection_changed.connect(self._on_segment_selected)
        self.segment_table.content_edited.connect(self._mark_dirty)
        self.segment_table.open_file_requested.connect(self._on_open_segment_file)
        self.segment_table.btn_add.clicked.connect(self.add_segment)
        self.segment_table.btn_del.clicked.connect(self.delete_segments)
        self.segment_table.btn_up.clicked.connect(lambda: self.move_segments(-1))
        self.segment_table.btn_down.clicked.connect(lambda: self.move_segments(1))
        self.segment_table.btn_split.clicked.connect(self.split_current)
        self.segment_table.btn_check.clicked.connect(self.segment_table.mark_checked)
        self.param_panel.params_changed.connect(self._on_params_changed)
        self.param_panel.preset_apply.connect(self._apply_preset)
        self.param_panel.btn_preset_save.clicked.connect(self.save_preset)
        self.param_panel.btn_preset_del.clicked.connect(self.delete_preset)
        self.param_panel.btn_preset_import.clicked.connect(self.import_presets)
        self.param_panel.btn_preset_export.clicked.connect(self.export_presets)
        self.param_panel.btn_out_dir.clicked.connect(self.choose_out_dir)
        self.btn_apply_voice.clicked.connect(self.apply_voice_to_checked)
        self.player.open_system_requested.connect(self._open_in_system_player)

    # ----------------------------------------------------------- 引擎/音色
    def _engines(self) -> dict:
        return {
            "edge": get_engine("edge", proxy=self.cfg.proxy),
            "sapi": get_engine("sapi"),
            "azure": get_engine("azure", api_key=self.cfg.api_keys.get("azure", "")),
            "iflytek": get_engine("iflytek", api_key=self.cfg.api_keys.get("iflytek", "")),
            "tencent": get_engine("tencent", api_key=self.cfg.api_keys.get("tencent", "")),
        }

    def _load_engines_and_voices(self) -> None:
        # 先显示内置兜底清单（列表不空白），后台拉取成功后自动刷新
        from ..core.registry import builtin_edge_voices

        self.registry.replace_engine_voices("edge", builtin_edge_voices())
        self._on_voices_loaded(self.registry.list_voices(), [])
        self.queue_panel.append("正在从 Edge-TTS 拉取完整音色清单……（失败时保留内置清单）")
        worker = VoiceLoadWorker(self._engines(), self.registry)
        worker.done.connect(self._on_voices_loaded)
        worker.failed.connect(lambda e: self.queue_panel.append(f"音色加载失败：{e}"))
        self._workers.append(worker)
        worker.start()

    def _on_voices_loaded(self, voices: list, warnings: list) -> None:
        self._voices = voices
        self._voice_names = {v.id: v.name for v in voices}
        favorites = {v.id for v in voices if self.registry.is_favorite(v.id)}
        self.voice_panel.set_voices(voices, favorites)
        self.segment_voice.clear()
        self.segment_voice.addItem("（项目默认音色）", "")
        for v in voices:
            self.segment_voice.addItem(f"{v.name} [{v.id}]", v.id)
        self.segment_table.set_segments(self.project.segments, self._voice_names)
        for w in warnings:
            self.queue_panel.append(f"⚠ {w}")
        self.queue_panel.append(f"音色加载完成：{len(voices)} 个")
        # 默认音色
        if not self.project.default_voice and voices:
            for v in voices:
                if "YunxiNeural" in v.id:
                    self.project.default_voice = v.id
                    break
            else:
                self.project.default_voice = voices[0].id
        self.statusBar().showMessage(f"当前音色：{self._voice_names.get(self.project.default_voice, self.project.default_voice)}")

    # ------------------------------------------------------------ 项目管理
    def new_project(self) -> None:
        self.project = Project(project="未命名项目")
        self.project_path = None
        self.project.default_voice = self.project.default_voice or ""
        self._refresh_segments()
        self._dirty = False

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", "配音项目 (*.dvproj)")
        if not path:
            return
        self._load_project_file(Path(path))

    def _load_project_file(self, path: Path) -> None:
        try:
            self.project = load_project(path)
        except ValueError as exc:
            QMessageBox.critical(self, "项目无法读取", str(exc))
            return
        self.project_path = path
        if self.project.extra.get("recovered_from_backup"):
            QMessageBox.information(self, "崩溃恢复",
                                    "检测到项目文件损坏，已从自动备份恢复最近的可用版本。")
        self.param_panel.set_params(self.project.preset or {})
        self.param_panel.out_dir.setText(self.project.output_dir or "out")
        self._refresh_segments()
        self._dirty = False
        self.cfg.last_project = str(path)
        self.statusBar().showMessage(f"已打开：{path.name}（{len(self.project.segments)} 段）")

    def save_project(self) -> bool:
        if not self.project_path:
            return self.save_project_as()
        self._collect_project_params()
        save_project(self.project_path, self.project)
        self._dirty = False
        self.statusBar().showMessage(f"已保存：{self.project_path}")
        return True

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "保存项目", "配音项目.dvproj", "配音项目 (*.dvproj)")
        if not path:
            return False
        self.project_path = Path(path)
        return self.save_project()

    def _autosave(self) -> None:
        if self._dirty and self.project_path:
            self._collect_project_params()
            save_project(self.project_path, self.project)
            self._dirty = False
            self.statusBar().showMessage(
                f"已自动保存 {dt.datetime.now().strftime('%H:%M:%S')}（每 {self.cfg.autosave_sec} 秒）"
            )

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _restore_session(self) -> None:
        """启动时恢复上次项目/崩溃恢复。"""
        last = self.cfg.last_project
        if last and Path(last).exists():
            self._load_project_file(Path(last))
        else:
            self.new_project()

    def _collect_project_params(self) -> None:
        p = self.param_panel.get_params()
        self.project.preset = p
        self.project.output_dir = self.param_panel.out_dir.text().strip() or "out"

    def _refresh_segments(self) -> None:
        self.segment_table.set_segments(self.project.segments, self._voice_names)

    # --------------------------------------------------------------- 导入
    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入 CSV", "", "CSV (*.csv)")
        if not path:
            return
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        reader = csv.reader(text.splitlines())
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return
        # 头行识别
        header = [c.strip() for c in rows[0]]
        has_header = any(h in header for h in ("序号", "文本", "text", "文本内容"))
        start = 1 if has_header else 0

        def col(*names: str) -> int:
            for n in names:
                if n in header:
                    return header.index(n)
            return -1

        ci_i, ci_file, ci_text = col("序号", "i"), col("文件名", "name"), col("文本", "text", "文本内容")
        ci_voice, ci_note = col("音色", "voice"), col("备注", "note")
        ci_rate, ci_pitch, ci_vol = col("语速"), col("音调"), col("音量")
        added = 0
        for row in rows[start:]:
            def cell(idx: int) -> str:
                return row[idx].strip() if 0 <= idx < len(row) else ""

            text_val = cell(ci_text) if ci_text >= 0 else (row[2].strip() if len(row) > 2 else "")
            if not text_val and len(row) == 1:
                text_val = row[0].strip()
            if not text_val:
                continue
            idx_val = int(cell(ci_i)) if cell(ci_i).isdigit() else self.project.next_index()
            seg = Segment(
                i=idx_val,
                name=cell(ci_note) or cell(ci_file),
                text=text_val,
                voice=self._resolve_voice_id(cell(ci_voice)),
                filename=cell(ci_file) if ci_file != ci_note else "",
                note=cell(ci_note),
            )
            params = {}
            if cell(ci_rate):
                try:
                    params["rate"] = float(cell(ci_rate).replace("%", "")) / (100 if "%" in cell(ci_rate) else 1)
                except ValueError:
                    pass
            if cell(ci_pitch):
                try:
                    params["pitch"] = float(cell(ci_pitch))
                except ValueError:
                    pass
            if cell(ci_vol):
                try:
                    params["volume"] = float(cell(ci_vol))
                except ValueError:
                    pass
            seg.params = params
            self.project.segments.append(seg)
            added += 1
        self.project.segments.sort(key=lambda s: s.i)
        self._refresh_segments()
        self._mark_dirty()
        self.statusBar().showMessage(f"已导入 {added} 段")

    def import_txt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入 TXT / Markdown", "", "文本 (*.txt *.md)")
        if not path:
            return
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        # 空行分段；Markdown 标题做段名
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        added = 0
        for block in blocks:
            name = ""
            body = block
            if block.lstrip().startswith("#"):
                lines = block.splitlines()
                name = lines[0].lstrip("# ").strip()
                body = "\n".join(lines[1:]).strip() or name
            self.project.segments.append(Segment(i=self.project.next_index(), name=name, text=body))
            added += 1
        self._refresh_segments()
        self._mark_dirty()
        self.statusBar().showMessage(f"已导入 {added} 段")

    def _resolve_voice_id(self, name_or_id: str) -> str:
        """CSV 音色列 → voice_id（支持 id 或中文名）。"""
        s = (name_or_id or "").strip()
        if not s:
            return ""
        for v in self._voices:
            if v.id == s or s in v.name or v.name.startswith(s):
                return v.id
        return s

    # ------------------------------------------------------------ 段操作
    def add_segment(self) -> None:
        self.project.segments.append(Segment(i=self.project.next_index(), text=""))
        self._refresh_segments()
        self._mark_dirty()

    def delete_segments(self) -> None:
        seg = self.segment_table.current_segment()
        if not seg:
            return
        self.project.segments = [s for s in self.project.segments if s is not seg]
        for n, s in enumerate(sorted(self.project.segments, key=lambda x: x.i), start=1):
            s.i = n
        self._refresh_segments()
        self._mark_dirty()

    def move_segments(self, delta: int) -> None:
        seg = self.segment_table.current_segment()
        if not seg:
            return
        segs = sorted(self.project.segments, key=lambda x: x.i)
        idx = segs.index(seg)
        j = idx + delta
        if 0 <= j < len(segs):
            segs[idx], segs[j] = segs[j], segs[idx]
            for n, s in enumerate(segs, start=1):
                s.i = n
            self.project.segments = segs
            self._refresh_segments()
            self._mark_dirty()

    def split_current(self) -> None:
        """拆句（FR-T-8）：长段按句号切成多条。"""
        seg = self.segment_table.current_segment()
        if not seg or not seg.text.strip():
            return
        parts = split_sentences(seg.text, max_chars=10 ** 6)
        if len(parts) <= 1:
            QMessageBox.information(self, "拆句", "当前段无需拆分（只有一个句子）")
            return
        segs = sorted(self.project.segments, key=lambda x: x.i)
        idx = segs.index(seg)
        segs.pop(idx)
        new_segs = [Segment(i=0, name=seg.display_name(), text=p, voice=seg.voice,
                            params=dict(seg.params)) for p in parts]
        segs[idx:idx] = new_segs
        for n, s in enumerate(segs, start=1):
            s.i = n
        self.project.segments = segs
        self._refresh_segments()
        self._mark_dirty()

    def apply_voice_to_checked(self) -> None:
        vid = self.segment_voice.currentData() or ""
        ids = set(self.segment_table.selected_indexes())
        for s in self.project.segments:
            if s.i in ids:
                s.voice = vid
        self._refresh_segments()
        self._mark_dirty()

    # ------------------------------------------------------------ 音色动作
    def _on_voice_selected(self, voice_id: str) -> None:
        self.project.default_voice = voice_id
        self.statusBar().showMessage(f"当前音色：{self._voice_names.get(voice_id, voice_id)}")
        # 参数随音色记忆（FR-P-13）
        if self.cfg.remember_voice_params:
            remembered = self.registry.get_voice_params(voice_id)
            if remembered:
                self.param_panel.set_params(remembered)
                self.statusBar().showMessage(f"已带出音色「{self._voice_names.get(voice_id, voice_id)}」的记忆参数")
        caps = {}
        engine_name = voice_id.split(":", 1)[0]
        try:
            caps = get_engine(engine_name, proxy=self.cfg.proxy).capabilities()
        except Exception:  # noqa: BLE001
            caps = {}
        self.param_panel.set_style_supported(bool(caps.get("style")), str(caps.get("note", "")))
        self.param_panel.set_pitch_volume_supported(
            bool(caps.get("pitch", True)), bool(caps.get("volume", True)),
            str(caps.get("note", "")) if not caps.get("pitch", True) else "",
        )
        self._mark_dirty()

    def _on_preview(self, voice_id: str) -> None:
        self.queue_panel.append(f"正在生成试听：{self._voice_names.get(voice_id, voice_id)} …")
        worker = PreviewWorker(self._engines(), self.registry, self.cfg.cache_path(), voice_id)
        worker.ready.connect(self._on_preview_ready)
        worker.failed.connect(lambda vid, e: self.queue_panel.append(f"⚠ 试听失败：{e}"))
        self._workers.append(worker)
        worker.start()

    def _on_preview_ready(self, voice_id: str, path: str) -> None:
        self.queue_panel.append(f"试听就绪：{self._voice_names.get(voice_id, voice_id)}")
        self.player.load(path, autoplay=True)

    def _on_favorite(self, voice_id: str) -> None:
        self.registry.toggle_favorite(voice_id)
        favorites = {v.id for v in self._voices if self.registry.is_favorite(v.id)}
        self.voice_panel.set_voices(self._voices, favorites)

    def _on_segment_selected(self, seg) -> None:
        if seg and seg.voice:
            idx = self.segment_voice.findData(seg.voice)
            if idx >= 0:
                self.segment_voice.setCurrentIndex(idx)

    # ------------------------------------------------------------ 参数动作
    def _on_params_changed(self, params: dict) -> None:
        self._mark_dirty()
        if self.cfg.remember_voice_params and self.project.default_voice:
            merged = dict(params)
            self.registry.set_voice_params(self.project.default_voice, merged)

    def _apply_preset(self, name: str) -> None:
        self.param_panel.apply_preset(name, self.user_presets)
        self.statusBar().showMessage(f"已套用预设：{name}")

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "另存预设", "预设名称：")
        if ok and name.strip():
            self.user_presets[name.strip()] = self.param_panel.get_params()
            self.param_panel.preset_combo.addItem(name.strip())
            self.param_panel.preset_combo.setCurrentText(name.strip())
            self._save_user_presets()

    def delete_preset(self) -> None:
        name = self.param_panel.preset_combo.currentText()
        if name in BUILTIN_PRESETS:
            QMessageBox.information(self, "删除预设", "内置预设不可删除")
            return
        self.user_presets.pop(name, None)
        self.param_panel.preset_combo.removeItem(self.param_panel.preset_combo.currentIndex())
        self._save_user_presets()

    def import_presets(self) -> None:
        import json

        path, _ = QFileDialog.getOpenFileName(self, "导入预设", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for k, v in data.items():
                self.user_presets[k] = v
                self.param_panel.preset_combo.addItem(k)
            self._save_user_presets()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入预设", f"导入失败：{exc}")

    def export_presets(self) -> None:
        import json

        path, _ = QFileDialog.getSaveFileName(self, "导出预设", "预设.json", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(self.user_presets, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_user_presets(self) -> None:
        import json

        from ..core.config import user_data_dir

        p = user_data_dir() / "presets.json"
        if p.exists():
            try:
                self.user_presets = json.loads(p.read_text(encoding="utf-8"))
                for name in self.user_presets:
                    self.param_panel.preset_combo.addItem(name)
            except (OSError, json.JSONDecodeError):
                self.user_presets = {}

    def _save_user_presets(self) -> None:
        import json

        from ..core.config import user_data_dir

        p = user_data_dir() / "presets.json"
        p.write_text(json.dumps(self.user_presets, ensure_ascii=False, indent=2), encoding="utf-8")

    def choose_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.param_panel.out_dir.setText(d)

    # --------------------------------------------------------------- 生成
    def _output_dir(self) -> Path:
        self._collect_project_params()
        base = self.project_path.parent if self.project_path else Path.cwd()
        out = Path(self.project.output_dir or "out")
        return out if out.is_absolute() else (base / out)

    def _start_generation(self, segments: list[Segment], resume: bool, force: bool) -> None:
        if not segments:
            QMessageBox.information(self, "生成", "没有需要生成的段落")
            return
        if self._generate_worker and self._generate_worker.isRunning():
            QMessageBox.information(self, "生成", "已有生成任务在进行中，请先取消或等待完成")
            return
        self._collect_project_params()
        out_dir = self._output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        engines = self._engines()
        queue = JobQueue(engines, fallback_to_sapi=self.cfg.fallback_to_sapi,
                         retry_times=self.cfg.retry_times)
        self._generate_worker = GenerateWorker(queue, self.project, segments, out_dir, resume, force)
        self._generate_worker.event.connect(self._on_queue_event)
        self._generate_worker.finished_ok.connect(self._on_generation_done)
        self._workers.append(self._generate_worker)
        self.queue_panel.set_total(len(segments))
        self.act_gen_all.setEnabled(False)
        self.act_gen_sel.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_retry.setEnabled(False)
        self.act_cancel.setEnabled(True)
        self.queue_panel.append(f"—— 开始生成 {len(segments)} 段 → {out_dir} ——")
        self._generate_worker.start()

    def generate_all(self) -> None:
        self._start_generation(list(self.project.segments), resume=True, force=False)

    def generate_selected(self) -> None:
        ids = set(self.segment_table.selected_indexes())
        segs = [s for s in self.project.segments if s.i in ids] or (
            [self.segment_table.current_segment()] if self.segment_table.current_segment() else []
        )
        self._start_generation([s for s in segs if s], resume=True, force=False)

    def redo_current(self) -> None:
        seg = self.segment_table.current_segment()
        if not seg:
            QMessageBox.information(self, "只重做这一段", "请先在表格中选中一段")
            return
        self._start_generation([seg], resume=False, force=True)

    def retry_failed(self) -> None:
        segs = [s for s in self.project.segments if s.status == STATUS_FAILED]
        self._start_generation(segs, resume=False, force=True)

    def cancel_generation(self) -> None:
        if self._generate_worker:
            self._generate_worker.queue.cancel()
            self.queue_panel.append("正在取消……（完成后不留半截文件）")

    def _on_queue_event(self, ev) -> None:
        done = ev.data.get("pos", -1) + 1 if ev.data else 0
        total = ev.total or 1
        eta_text = ""
        if ev.type in ("item_done", "item_skipped") and ev.data.get("elapsed") is not None:
            elapsed = ev.data.get("elapsed", 0)
            remaining = max(0, total - done)
            eta_text = f"预计剩余 {int(elapsed * remaining // 60)} 分 {int(elapsed * remaining % 60)} 秒"
        if ev.type == "item_done":
            self.queue_panel.set_progress(done, total, ev.message, eta_text)
            self.segment_table.refresh_status(ev.index)
            self.queue_panel.append(f"✓ {ev.message} → {ev.data.get('file', '')} "
                                    f"（{ev.data.get('duration', 0):.1f}s）")
            for w in ev.data.get("warnings") or []:
                self.queue_panel.append(f"  ⚠ {w}")
        elif ev.type == "item_skipped":
            self.queue_panel.set_progress(done, total, ev.message, eta_text)
            self.queue_panel.append(f"⏭ {ev.message}")
        elif ev.type == "item_start":
            self.queue_panel.set_progress(max(0, done - 1), total, ev.message, eta_text)
        elif ev.type == "item_failed":
            self.queue_panel.set_progress(done, total, ev.message)
            self.segment_table.refresh_status(ev.index)
            self.queue_panel.append(f"✗ {ev.message}")
        elif ev.type == "warning":
            self.queue_panel.append(f"⚠ {ev.message}")
        elif ev.type == "finished":
            self.queue_panel.append(ev.message)
        # 清单/项目回写（崩溃可恢复）
        if ev.type in ("item_done", "item_failed", "item_skipped"):
            self._mark_dirty()
            if self.project_path:
                save_project(self.project_path, self.project)

    def _on_generation_done(self, summary) -> None:
        self.act_gen_all.setEnabled(True)
        self.act_gen_sel.setEnabled(True)
        self.act_redo.setEnabled(True)
        self.act_retry.setEnabled(True)
        self.act_cancel.setEnabled(False)
        self._refresh_segments()
        self._mark_dirty()
        if self.project_path:
            save_project(self.project_path, self.project)
        # 语速校准（FR-T-1）
        for seg in self.project.segments:
            if seg.duration > 0 and seg.text.strip():
                n = count_chars(seg.text)
                if n > 10:
                    self.registry.set_chars_per_sec(seg.voice or self.project.default_voice,
                                                    n / seg.duration)
        out = QMessageBox(self)
        out.setWindowTitle("生成完成")
        out.setIcon(QMessageBox.Information)
        out.setText(f"成功 {summary.done} 条，跳过 {summary.skipped} 条，失败 {summary.failed} 条")
        detail = (f"音频总时长 {summary.total_duration / 60:.1f} 分钟\n"
                  f"总大小 {summary.total_bytes / 1024 / 1024:.1f} MB\n"
                  f"耗时 {summary.elapsed / 60:.1f} 分钟\n\n")
        if summary.failures:
            detail += "异常清单（可复制）：\n" + "\n".join(f"第 {i} 段：{e}" for i, e in summary.failures)
        out.setDetailedText(detail)
        out.exec()

    # --------------------------------------------------------------- 导出
    def export_delivery(self) -> None:
        out_dir = self._output_dir()
        if not out_dir.exists() or not any(out_dir.glob("*.*")):
            QMessageBox.information(self, "导出交付包", "还没有生成过音频，请先「生成全部」")
            return
        default = str(out_dir.parent / f"{self.project.project}_交付包.zip")
        path, _ = QFileDialog.getSaveFileName(self, "导出交付包", default, "ZIP (*.zip)")
        if not path:
            return
        self._collect_project_params()
        zip_path = export_delivery_zip(self.project, out_dir, path)
        QMessageBox.information(self, "导出交付包",
                                f"已导出：{zip_path}\n\n内容：音频 + 清单.csv + 参数.json + README.txt"
                                f"（含 AI 合成语音声明）")

    def _on_open_segment_file(self, index: int) -> None:
        seg = self.project.find(index)
        if not seg or not seg.file:
            return
        path = self._output_dir() / seg.file
        if path.exists():
            self.player.load(str(path), autoplay=True)
            self._open_in_system_player(str(path))
        else:
            self.statusBar().showMessage(f"文件不存在：{path}（可能尚未生成）")

    def _open_in_system_player(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_lexicon(self) -> None:
        """编辑发音词典与读法规则。"""
        from .lexicon_dialog import LexiconDialog

        dlg = LexiconDialog(self.project.lexicon_obj(), self)
        if dlg.exec():
            self.project.lexicon = dlg.result_lexicon().to_dict()
            self._mark_dirty()
            self.statusBar().showMessage("发音词典已更新（对之后的生成生效，词典变化会触发相关段重做）")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.statusBar().showMessage("设置已保存（部分设置重启后生效）")

    # --------------------------------------------------------------- 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._generate_worker and self._generate_worker.isRunning():
            self._generate_worker.queue.cancel()
            self._generate_worker.wait(3000)
        if self._dirty and self.project_path:
            self._collect_project_params()
            save_project(self.project_path, self.project)
        self.registry.close()
        event.accept()
