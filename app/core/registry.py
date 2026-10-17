"""音色注册表（FR-V-1~6、FR-P-13）：SQLite 缓存音色清单、收藏、随音色记忆的参数。

数据文件 ``voices.db`` 存用户目录。音色来源：各引擎动态拉取（失败时用内置清单兜底）。
线程安全：音色加载/试听在工作线程执行，所有数据库操作用锁串行化。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from ..engines.base import Voice
from .config import user_data_dir

#: 内置常用中文音色兜底清单（需求文档 §4.1 初版清单，接口拉取失败时用）
BUILTIN_EDGE_VOICES: list[dict] = [
    {"id": "edge:zh-CN-YunxiNeural", "name": "云希（少年男声）", "gender": "male", "tags": ["旁白", "解说", "阳光"]},
    {"id": "edge:zh-CN-YunjianNeural", "name": "云健（稳重男声）", "gender": "male", "tags": ["新闻", "体育"]},
    {"id": "edge:zh-CN-YunyangNeural", "name": "云扬（明亮男声）", "gender": "male", "tags": ["解说", "新闻"]},
    {"id": "edge:zh-CN-YunxiaNeural", "name": "云夏（童声男声）", "gender": "male", "tags": ["学生", "童声"]},
    {"id": "edge:zh-CN-XiaoxiaoNeural", "name": "晓晓（温柔女声）", "gender": "female", "tags": ["旁白", "温柔"]},
    {"id": "edge:zh-CN-XiaoyiNeural", "name": "晓伊（甜美女声）", "gender": "female", "tags": ["学生", "甜美"]},
    {"id": "edge:zh-CN-XiaoshuangNeural", "name": "晓双（活泼女声）", "gender": "female", "tags": ["活泼", "童声"]},
    {"id": "edge:zh-CN-XiaochenNeural", "name": "晓辰（知性女声）", "gender": "female", "tags": ["知性", "旁白"]},
    {"id": "edge:zh-CN-liaoning-XiaobeiNeural", "name": "晓北（东北话）", "gender": "female", "tags": ["方言", "趣味"]},
    {"id": "edge:zh-CN-shaanxi-XiaoniNeural", "name": "晓妮（陕西话）", "gender": "female", "tags": ["方言", "情景"]},
    {"id": "edge:zh-HK-HiuMaanNeural", "name": "晓曼（粤语女声）", "gender": "female", "tags": ["粤语"]},
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY,
    name TEXT, engine TEXT, lang TEXT, gender TEXT,
    tags TEXT, custom INTEGER DEFAULT 0, sample_path TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS favorites (
    voice_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS voice_params (
    voice_id TEXT PRIMARY KEY,
    params TEXT
);
CREATE TABLE IF NOT EXISTS voice_meta (
    voice_id TEXT PRIMARY KEY,
    chars_per_sec REAL DEFAULT 4.2
);
"""


class VoiceRegistry:
    """音色注册表（SQLite，线程安全）。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else user_data_dir() / "voices.db"
        # check_same_thread=False：工作线程（音色加载/试听）也要写库，用锁串行化
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            self._conn.close()

    # -- 音色 --------------------------------------------------------------
    def replace_engine_voices(self, engine: str, voices: list[Voice]) -> None:
        """整批刷新某引擎的音色（保留收藏与记忆参数）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM voices WHERE engine=?", (engine,))
            for v in voices:
                cur.execute(
                    "INSERT OR REPLACE INTO voices (id,name,engine,lang,gender,tags,custom,sample_path) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (v.id, v.name, v.engine, v.lang, v.gender, json.dumps(v.tags, ensure_ascii=False),
                     int(v.custom), v.sample_path),
                )
            self._conn.commit()

    def list_voices(self) -> list[Voice]:
        """列出全部音色（收藏在前）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT v.*, COALESCE(f.voice_id,'') AS fav FROM voices v "
                "LEFT JOIN favorites f ON f.voice_id = v.id "
                "ORDER BY fav DESC, v.engine, v.lang, v.gender, v.name"
            ).fetchall()
        out: list[Voice] = []
        for r in rows:
            out.append(
                Voice(
                    id=r[0], name=r[1], engine=r[2], lang=r[3] or "zh-CN",
                    gender=r[4] or "", tags=json.loads(r[5] or "[]"),
                    sample_path=r[7] or "", custom=bool(r[6]),
                )
            )
        return out

    def search(self, keyword: str) -> list[Voice]:
        """按中文名/英文 ID/标签搜索。"""
        kw = (keyword or "").strip().lower()
        if not kw:
            return self.list_voices()
        return [v for v in self.list_voices()
                if kw in v.name.lower() or kw in v.id.lower() or any(kw in t.lower() for t in v.tags)]

    def get_voice(self, voice_id: str) -> Voice | None:
        """按 id 取音色。"""
        for v in self.list_voices():
            if v.id == voice_id:
                return v
        return None

    # -- 收藏（FR-V-2 置顶）------------------------------------------------
    def is_favorite(self, voice_id: str) -> bool:
        """是否已收藏。"""
        with self._lock:
            r = self._conn.execute("SELECT 1 FROM favorites WHERE voice_id=?", (voice_id,)).fetchone()
        return r is not None

    def toggle_favorite(self, voice_id: str) -> bool:
        """切换收藏状态，返回是否已收藏。"""
        with self._lock:
            if self._conn.execute("SELECT 1 FROM favorites WHERE voice_id=?", (voice_id,)).fetchone():
                self._conn.execute("DELETE FROM favorites WHERE voice_id=?", (voice_id,))
                self._conn.commit()
                return False
            self._conn.execute("INSERT OR REPLACE INTO favorites VALUES (?)", (voice_id,))
            self._conn.commit()
            return True

    # -- 随音色记忆的参数（FR-P-13）----------------------------------------
    def get_voice_params(self, voice_id: str) -> dict:
        """取该音色上次使用的参数。"""
        with self._lock:
            r = self._conn.execute("SELECT params FROM voice_params WHERE voice_id=?", (voice_id,)).fetchone()
        if not r:
            return {}
        try:
            return json.loads(r[0] or "{}")
        except json.JSONDecodeError:
            return {}

    def set_voice_params(self, voice_id: str, params: dict) -> None:
        """记忆该音色的参数。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO voice_params (voice_id, params) VALUES (?,?)",
                (voice_id, json.dumps(params, ensure_ascii=False)),
            )
            self._conn.commit()

    def chars_per_sec(self, voice_id: str) -> float:
        """该音色的语速校准值（字/秒，中文默认 4.2，FR-T-1）。"""
        with self._lock:
            r = self._conn.execute("SELECT chars_per_sec FROM voice_meta WHERE voice_id=?", (voice_id,)).fetchone()
        return float(r[0]) if r and r[0] else 4.2

    def set_chars_per_sec(self, voice_id: str, cps: float) -> None:
        """按实测校准语速（实际时长回填后可自动调用）。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO voice_meta (voice_id, chars_per_sec) VALUES (?,?)", (voice_id, float(cps))
            )
            self._conn.commit()

    def set_sample_path(self, voice_id: str, path: str) -> None:
        """记录试听样音缓存路径。"""
        with self._lock:
            self._conn.execute("UPDATE voices SET sample_path=? WHERE id=?", (path, voice_id))
            self._conn.commit()


def builtin_edge_voices() -> list[Voice]:
    """内置 Edge-TTS 音色兜底清单。"""
    return [
        Voice(id=d["id"], name=d["name"], engine="edge", lang="zh-CN",
              gender=d["gender"], tags=list(d["tags"]))
        for d in BUILTIN_EDGE_VOICES
    ]


def voice_as_dict(v: Voice) -> dict:
    """Voice → dict（UI/项目用）。"""
    return asdict(v)
