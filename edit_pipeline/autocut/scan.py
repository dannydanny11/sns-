"""입력 폴더 구조 스캔.

input/<프로젝트>/
  ├─ cam_front/ ...            (단일 폴더형: 카메라 폴더가 바로 아래)
  ├─ <장소>/cam_front/ ...     (장소별 폴더형)
  └─ audio/                    (별도 녹음. 장소 폴더 안의 audio/ 가 있으면 그쪽 우선)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .media import AUDIO_EXT, VIDEO_EXT


@dataclass
class Camera:
    name: str          # 폴더명 (cam_front)
    role: str          # front / side / tele / wide / two ...
    files: list[str] = field(default_factory=list)


@dataclass
class Place:
    name: str
    path: str
    cameras: list[Camera] = field(default_factory=list)
    audio_files: list[str] = field(default_factory=list)


def camera_role(folder: str) -> str:
    """cam_side2 → side, cam_front → front."""
    name = folder.lower()
    if name.startswith("cam_"):
        name = name[4:]
    return name.rstrip("0123456789_-") or name


def _media_files(path: str, exts: set[str]) -> list[str]:
    out = []
    for entry in sorted(os.listdir(path)):
        full = os.path.join(path, entry)
        if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in exts and not entry.startswith("."):
            out.append(os.path.abspath(full))
    return out


def _cameras_in(path: str) -> list[Camera]:
    cams = []
    for entry in sorted(os.listdir(path)):
        full = os.path.join(path, entry)
        if os.path.isdir(full) and entry.lower().startswith("cam"):
            files = _media_files(full, VIDEO_EXT)
            cams.append(Camera(name=entry, role=camera_role(entry), files=files))
    return cams


def scan_project(project_dir: str) -> tuple[list[Place], list[str]]:
    """장소 목록과 경고(누락 등)를 돌려준다."""
    warnings: list[str] = []
    if not os.path.isdir(project_dir):
        raise FileNotFoundError(project_dir)
    project_audio_dir = os.path.join(project_dir, "audio")
    project_audio = _media_files(project_audio_dir, AUDIO_EXT) if os.path.isdir(project_audio_dir) else []

    places: list[Place] = []
    direct = _cameras_in(project_dir)
    if direct:
        places.append(Place(name=os.path.basename(os.path.normpath(project_dir)), path=project_dir, cameras=direct))
    for entry in sorted(os.listdir(project_dir)):
        full = os.path.join(project_dir, entry)
        if not os.path.isdir(full) or entry.lower() == "audio" or entry.lower().startswith("cam"):
            continue
        cams = _cameras_in(full)
        if cams:
            place_audio_dir = os.path.join(full, "audio")
            audio = _media_files(place_audio_dir, AUDIO_EXT) if os.path.isdir(place_audio_dir) else []
            places.append(Place(name=entry, path=full, cameras=cams, audio_files=audio))

    for place in places:
        if not place.audio_files:
            place.audio_files = list(project_audio)
        for cam in place.cameras:
            if not cam.files:
                warnings.append(f"[{place.name}] {cam.name} 폴더에 영상 파일 없음")
        if not place.audio_files:
            warnings.append(f"[{place.name}] 별도 녹음 파일 없음 → 정면 카메라 오디오를 기준으로 사용")
    if not places:
        warnings.append("cam_* 폴더를 찾지 못함")
    return places, warnings
