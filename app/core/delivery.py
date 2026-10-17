"""导出交付包（FR-S-5）：音频 + 清单.csv + 参数.json + README.txt → zip。

合规（硬性红线 7）：README.txt 中必须包含「AI 合成语音」声明与
「仅可克隆本人或已获授权声音」提示（P2 预留）。
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from pathlib import Path

from .config import APP_NAME, APP_VERSION
from .project import Project
from .queue import build_params, write_manifest

#: 交付包 README 文案（含合规声明）
DELIVERY_README = """【{project}】配音交付包
生成软件：{app} v{version}
生成时间：{time}

一、内容说明
- *.mp3 / *.wav / *.flac：各段配音音频，文件名与清单.csv 一一对应；
- 清单.csv：序号 / 文件名 / 音色 / 字数 / 时长 / 文件路径（UTF-8 BOM，Excel 可直接打开）；
- 参数.json：本次生成的全部参数（音色、语速、音调、采样率、码率、响度等），可供复现。

二、使用建议
1. 将音频按序号导入剪映 / Premiere，与画面对轨；
2. 如需重做某一段，回到 TTS Studio 修改对应段落再点「只重做这一段」，
   其余文件不会被打乱；
3. 全部音频规格已统一（采样率、声道、响度一致），可直接拼接。

三、合规声明（重要）
- 本音频为 AI 合成语音（TTS 生成），不是真人录音，请勿用于冒充他人；
- 声音克隆功能（下期）仅可用于克隆本人或已获授权的声音，违者自负法律责任；
- 请遵守学校与平台关于 AI 生成内容标识的相关要求。
"""


def export_delivery_zip(
    project: Project,
    output_dir: str | Path,
    zip_path: str | Path,
    *,
    app_name: str = APP_NAME,
    app_version: str = APP_VERSION,
) -> Path:
    """把 ``output_dir`` 里的成品音频与清单/参数/说明打包为交付 zip。

    :param project: 项目（提供段列表与参数）
    :param output_dir: 成品目录（含已生成音频）
    :param zip_path: 目标 zip 路径
    :returns: zip 实际路径
    """
    out_dir = Path(output_dir)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # 清单与参数以打包时刻为准重新落盘，保证与音频一致（红线 6）
    manifest = write_manifest(out_dir, project)
    now = dt.datetime.now()
    params_doc = {
        "app": app_name,
        "version": app_version,
        "generated_at": now.isoformat(timespec="seconds"),
        "project": project.project,
        "default_voice": project.default_voice,
        "preset": project.preset,
        "lexicon": project.lexicon,
        "segments": [
            {
                "i": s.i,
                "name": s.display_name(),
                "voice": s.voice or project.default_voice,
                "text": s.text,
                "params": build_params(project, s),
                "file": s.file,
                "duration": round(s.duration, 3),
                "chars": len(s.text.strip()),
            }
            for s in sorted(project.segments, key=lambda x: x.i)
        ],
    }
    readme = DELIVERY_README.format(
        project=project.project, app=app_name, version=app_version,
        time=now.strftime("%Y-%m-%d %H:%M:%S"),
    )

    tmp_json = out_dir / "参数.json"
    tmp_readme = out_dir / "README.txt"
    tmp_json.write_text(json.dumps(params_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_readme.write_text(readme, encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for seg in sorted(project.segments, key=lambda x: x.i):
            if seg.file and (out_dir / seg.file).exists():
                zf.write(out_dir / seg.file, arcname=f"audio/{Path(seg.file).name}")
        zf.write(manifest, arcname="清单.csv")
        zf.write(tmp_json, arcname="参数.json")
        zf.write(tmp_readme, arcname="README.txt")
    return zip_path
