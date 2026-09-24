"""합성 촬영본 생성(테스트용).

별도 녹음 60초 + 카메라 3대:
  cam_front  녹음 시작 2.5초 뒤부터 55초 촬영
  cam_side   녹음 시작 0.8초 뒤부터 50초 촬영
  cam_tele   녹음과 무관한 소리(싱크 실패로 제외되어야 함)
전사는 Whisper 대신 가짜 전사 JSON 을 work/transcript 에 넣어 둔다.
"""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np
from scipy.io import wavfile

from autocut.media import ffmpeg_exe

SR = 48000
OFFSETS = {"cam_front": 2.5, "cam_side": 0.8}

SCRIPT = """# 들어가며
안녕하세요 선생님 여러분. 오늘은 **수업 설계의 세 가지 원칙**을 살펴보겠습니다.

# 첫 번째 원칙
첫 번째는 학습 목표를 먼저 정하는 것입니다. 목표가 분명해야 활동이 흔들리지 않습니다.
"""

# (문장, 뒤 쉼) — 두 번째 줄은 중간에 끊긴 NG, 마지막 문장은 두 번 읽음
TAKES = [
    ("안녕하세요 선생님 여러분.", 0.8),
    ("오늘은 수업 설계의", 1.5),
    ("음", 0.6),
    ("오늘은 수업 설계의 세 가지 원칙을 살펴보겠습니다.", 3.0),
    ("첫 번째는 학 학습 목표를", 0.7),
    ("먼저 정하는 것입니다.", 0.6),
    ("잠깐만요 다시 할게요", 1.0),
    ("목표가 분명해야 활동이 흔들리지 않습니다.", 0.5),
    ("목표가 분명해야 활동이 흔들리지 않습니다.", 1.0),
]


def fake_words(start: float = 4.0):
    words, t = [], start
    for text, pause in TAKES:
        for w in text.split():
            dur = 0.12 + 0.09 * len(w)
            words.append({"w": w, "s": round(t, 3), "e": round(t + dur, 3), "p": 0.95})
            t += dur + 0.06
        t += pause
    return words


def speech_like(words, total, rng):
    """단어 구간에만 소리가 있는 합성 신호(상관 계산이 가능하도록 무작위 스펙트럼)."""
    n = int(total * SR)
    sig = 0.003 * rng.normal(size=n)
    for w in words:
        a, b = int(w["s"] * SR), int(w["e"] * SR)
        seg = rng.normal(size=b - a)
        seg = np.convolve(seg, rng.normal(size=32) / 8, mode="same")
        sig[a:b] += 0.3 * seg * np.hanning(b - a)
    sig[int(1.0 * SR)] = 0.9  # 슬레이트(박수) — 녹음 1.0초 지점
    return sig


def _mux(video_out: str, wav: str, dur: float) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30000/1001:duration={dur}",
           "-i", wav,
           "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k", video_out]
    subprocess.run(cmd, check=True)


def build(root: str, project: str = "테스트세미지") -> dict:
    rng = np.random.default_rng(7)
    proj = os.path.join(root, "input", project)
    os.makedirs(os.path.join(proj, "audio"), exist_ok=True)
    words = fake_words()
    total = 60.0
    ref = speech_like(words, total, rng)
    ref_path = os.path.join(proj, "audio", "rec01.wav")
    wavfile.write(ref_path, SR, np.stack([ref, 0.7 * ref], axis=1).astype(np.float32))

    tmp = os.path.join(root, "tmp")
    os.makedirs(tmp, exist_ok=True)
    specs = {"cam_front": (2.5, 55.0), "cam_side": (0.8, 50.0), "cam_tele": (None, 40.0)}
    for cam, (off, dur) in specs.items():
        os.makedirs(os.path.join(proj, cam), exist_ok=True)
        if off is None:
            audio = 0.2 * rng.normal(size=int(dur * SR))
        else:
            seg = ref[int(off * SR): int((off + dur) * SR)]
            audio = 0.5 * np.convolve(seg, [0.6, 0.3, 0.1], "same") + 0.01 * rng.normal(size=len(seg))
        wav = os.path.join(tmp, f"{cam}.wav")
        wavfile.write(wav, SR, audio.astype(np.float32))
        _mux(os.path.join(proj, cam, "C0001.mp4"), wav, dur)

    script_path = os.path.join(root, "script", f"{project}.txt")
    os.makedirs(os.path.dirname(script_path), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(SCRIPT)

    tdir = os.path.join(root, "work", "transcript", project)
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, f"{project}__rec01.json"), "w", encoding="utf-8") as f:
        json.dump({"language": "ko", "duration": total, "words": words, "segments": []}, f, ensure_ascii=False)
    return {"project": proj, "script": script_path, "work": os.path.join(root, "work"),
            "output": os.path.join(root, "output")}


if __name__ == "__main__":
    import sys
    print(build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/autocut_fixture"))
