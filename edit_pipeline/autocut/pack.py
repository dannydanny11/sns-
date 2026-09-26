"""보낼 자료 가볍게 만들기: 촬영본 폴더를 싱크·전사·화자 비교에 필요한 만큼만 작게 복사한다.

- 영상: 180p 저화질 + 소리 그대로(파일 이름은 그대로, 확장자만 .mp4)
- 오디오(마이크·녹음기): FLAC 무손실 압축(확장자만 .flac)
- 폴더 구조 유지, 소니 XML 같은 작은 사이드카는 그대로 복사
- --minutes 를 주면 각 파일의 앞부분만(파일 0초 위치는 그대로라 셀렉츠 결과와 비교 가능)
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .autosort import collect
from .media import ffmpeg_exe

SIDECAR_EXT = {".xml"}


def pack(project_dir: str, out_dir: str, minutes: float | None = None, height: int = 180, log=print) -> dict:
    videos, audios, _ = collect(project_dir)
    os.makedirs(out_dir, exist_ok=True)
    limit = ["-t", f"{minutes * 60:.0f}"] if minutes else []
    done, failed, before, after = 0, [], 0, 0
    items = [(p, "video") for p in videos] + [(p, "audio") for p in audios]
    for k, (path, kind) in enumerate(items, 1):
        rel = os.path.relpath(path, project_dir)
        stem = os.path.splitext(rel)[0]
        dst = os.path.join(out_dir, stem + (".mp4" if kind == "video" else ".flac"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if kind == "video":
            codec = ["-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-map_metadata", "0"]
        else:
            codec = ["-vn", "-c:a", "flac", "-map_metadata", "0"]
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", path, *limit, *codec, dst]
        log(f"  [{k}/{len(items)}] {rel}")
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            failed.append(rel)
            continue
        before += os.path.getsize(path)
        after += os.path.getsize(dst)
        done += 1
    for root, _, files in os.walk(project_dir):   # 작은 사이드카(소니 XML 등): 카메라 기종·시리얼 판정용
        for name in files:
            if os.path.splitext(name)[1].lower() in SIDECAR_EXT and not name.startswith("."):
                src = os.path.join(root, name)
                if os.path.getsize(src) < 2_000_000:
                    dst = os.path.join(out_dir, os.path.relpath(src, project_dir))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
    return {"files": done, "failed": failed, "before_mb": round(before / 1e6, 1), "after_mb": round(after / 1e6, 1),
            "out": out_dir}
