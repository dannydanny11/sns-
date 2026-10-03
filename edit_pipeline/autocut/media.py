"""ffmpeg 호출: 실행 파일 탐색, 미디어 정보 조회, 오디오 추출."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction

import numpy as np

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".bwf"}


class MediaError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    """FFMPEG 환경변수 → PATH → imageio-ffmpeg 번들 순으로 찾는다."""
    env = os.environ.get("FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 설치 환경에 따라 다름
        raise MediaError("ffmpeg 를 찾을 수 없습니다. PATH 에 추가하거나 FFMPEG 환경변수를 지정하세요.") from exc


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    audio_channels: int | None = None
    start_tc: str | None = None  # 카메라가 기록한 시작 타임코드(있으면)
    vcodec: str | None = None
    created: str | None = None   # 촬영 시각(creation_time)
    model: str | None = None     # 카메라 모델(메타데이터·소니 XML)
    serial: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_VID = re.compile(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"([\d.]+)\s*fps")
_AUD = re.compile(r"Stream #.*Audio:.*?(\d+)\s*Hz,\s*([^,]+)")
_TC = re.compile(r"timecode\s*:\s*(\d{2}:\d{2}:\d{2}[:;]\d{2})")
_TAG = re.compile(r"^\s{4}([\w.:-]+)\s*:\s*(.+)$")
_VCODEC = re.compile(r"Video:\s*([\w-]+)")
_MODEL_KEYS = ("com.apple.quicktime.model", "model", "com.android.model", "product", "camera_model_name")


def _sidecar(path: str) -> dict:
    """소니 XML 사이드카(C0001M01.XML)에서 기종·시리얼을 읽는다."""
    stem, _ = os.path.splitext(path)
    for cand in (stem + "M01.XML", stem + "M01.xml"):
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8", errors="replace") as f:
                    text = f.read(20000)
            except OSError:
                return {}
            m = re.search(r'<Device[^>]*modelName="([^"]+)"[^>]*?(?:serialNo="([^"]+)")?', text)
            if m:
                return {"model": m.group(1), "serial": m.group(2)}
    return {}


def probe(path: str) -> MediaInfo:
    """ffprobe 없이 `ffmpeg -i` 의 stderr 를 해석한다(imageio 번들에는 ffprobe 가 없음)."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace")
    err = proc.stderr
    m = _DUR.search(err)
    if not m:
        raise MediaError(f"미디어 정보를 읽을 수 없음(손상 가능): {path}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    info = MediaInfo(path=path, duration=duration, has_video=False, has_audio=False)
    for line in err.splitlines():
        if "Video:" in line and not info.has_video and "attached pic" not in line:
            info.has_video = True
            vm = _VID.search(line)
            if vm:
                info.width, info.height = int(vm.group(1)), int(vm.group(2))
            fm = _FPS.search(line)
            if fm:
                info.fps = float(fm.group(1))
        elif "Audio:" in line and not info.has_audio:
            info.has_audio = True
            am = _AUD.search(line)
            if am:
                info.sample_rate = int(am.group(1))
                layout = am.group(2).strip()
                info.audio_channels = {"mono": 1, "stereo": 2}.get(layout, 2)
    tm = _TC.search(err)
    if tm:
        info.start_tc = tm.group(1)
    vc = _VCODEC.search(err)
    if vc:
        info.vcodec = vc.group(1)
    tags = {}
    for line in err.splitlines():
        m2 = _TAG.match(line)
        if m2:
            tags.setdefault(m2.group(1).lower(), m2.group(2).strip())
    info.created = tags.get("creation_time")
    info.model = next((tags[k] for k in _MODEL_KEYS if k in tags), None)
    if not info.model and "make" in tags:
        info.model = tags["make"]
    side = _sidecar(path)
    if side:
        info.model, info.serial = side.get("model") or info.model, side.get("serial")
    return info


def load_audio(path: str, sr: int = 16000, start: float | None = None, duration: float | None = None) -> np.ndarray:
    """모노 float32 PCM 으로 디코딩한다."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{max(start, 0):.3f}"]
    cmd += ["-i", path]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"오디오 추출 실패: {path}: {proc.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def extract_wav(path: str, out_path: str, sr: int = 16000) -> str:
    """전사용 모노 WAV 파일을 만든다."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", path,
           "-vn", "-ac", "1", "-ar", str(sr), out_path]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"WAV 추출 실패: {path}")
    return out_path


def timebase(fps: float | None) -> tuple[int, bool]:
    """FCP7 XML 의 (timebase, ntsc) 쌍. 29.97 → (30, True)."""
    if not fps:
        return 30, True
    for base in (24, 30, 60):
        if abs(fps - base * 1000 / 1001) < 0.02:
            return base, True
    return int(round(fps)), False


def frame_rate(fps: float | None) -> Fraction:
    base, ntsc = timebase(fps)
    return Fraction(base * 1000, 1001) if ntsc else Fraction(base)


def make_proxy(path: str, out_path: str, height: int = 360, codec: str = "h264") -> str:
    """브라우저 미리보기용 저해상도 프록시. 키프레임을 촘촘히 넣어 탐색을 빠르게 한다."""
    if os.path.exists(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(path):
        return out_path
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"] if codec == "h264" \
        else ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "600k"]
    acodec = ["-c:a", "aac", "-b:a", "96k"] if codec == "h264" else ["-c:a", "libopus", "-b:a", "64k"]
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", path,
           "-vf", f"scale=-2:{height}", "-g", "15", *vcodec, *acodec, out_path]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"프록시 생성 실패: {path}: {proc.stderr.decode(errors='replace')[-300:]}")
    return out_path
