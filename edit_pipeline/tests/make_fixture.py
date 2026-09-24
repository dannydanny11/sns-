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
    wavfile.write(ref_path, SR, (np.clip(np.stack([ref, 0.7 * ref], axis=1), -1, 1) * 32767).astype(np.int16))

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


# ─────────────────────────── 통째로 넣은 촬영본(자동 정리 테스트) ───────────────────────────

DUMP_SPEAKERS = ["TX01", "TX02", "TX03", "TX04"]


def _speech(words, total, rng):
    """단어 구간에만 소리가 있는 신호를 화자별로 따로 만든다."""
    n = int(total * SR)
    per = {s: np.zeros(n) for s in DUMP_SPEAKERS}
    for w in words:
        a, b = int(w["s"] * SR), int(w["e"] * SR)
        seg = np.convolve(rng.normal(size=b - a), rng.normal(size=32) / 8, mode="same")
        per[w["spk"]][a:b] += 0.3 * seg * np.hanning(b - a)
    return per


def dump_words(start: float, n_phr: int, seed: int):
    rng = np.random.default_rng(seed)
    words, t = [], start
    vocab = ["강릉은", "바다가", "정말", "아름답네요", "오늘", "여행을", "함께", "해서", "좋아요", "교과서에", "나온", "곳이에요"]
    for k in range(n_phr):
        spk = DUMP_SPEAKERS[k % 4]
        for _ in range(int(rng.integers(3, 6))):
            w = vocab[int(rng.integers(len(vocab)))]
            dur = 0.12 + 0.07 * len(w)
            words.append({"w": w, "s": round(t, 3), "e": round(t + dur, 3), "p": 0.9, "spk": spk})
            t += dur + 0.06
        words[-1]["w"] += "."
        t += 0.9
    return words


def _video(path, audio, dur, size="320x180", rate="30000/1001", created=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.wav"
    wavfile.write(tmp, SR, audio.astype(np.float32))
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={dur}", "-i", tmp, "-shortest",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k"]
    if created:
        cmd += ["-metadata", f"creation_time={created}"]
    subprocess.run(cmd + [path], check=True)
    os.remove(tmp)


def build_dump(root: str, project: str = "강릉") -> dict:
    """카드 폴더·무선 마이크 4개·분할 파일·인서트·잡파일이 섞인 촬영본 폴더.

    세션 1(60초): 카메라 4대(카드1 MVI_9447, 측면캠 MVI_0014, 카드3 캐논 시네마 2개로 분할, 카드4 CJ)
    세션 2(40초): 카드1 MVI_9448, 측면캠 MVI_0016
    인서트: 측면캠 MVI_0015(무관한 소리)
    """
    rng = np.random.default_rng(11)
    proj = os.path.join(root, "input", project)
    sessions = [("TX0{}_MIC028_20260711_101626_edit.WAV", 60.0, dump_words(3.0, 10, 1)),
                ("TX0{}_MIC029_20260711_113000_edit.WAV", 40.0, dump_words(2.0, 6, 2))]
    mic_offsets = {"TX01": 0.0, "TX02": 0.4, "TX03": -0.3, "TX04": 0.25}   # 마이크마다 녹음 시작이 다름
    truth = {}
    for si, (pattern, total, words) in enumerate(sessions):
        per = _speech(words, total + 1, rng)
        scene = sum(per.values())
        for k, spk in enumerate(DUMP_SPEAKERS):
            own = per[spk] + 0.12 * (scene - per[spk]) + 0.002 * rng.normal(size=len(scene))
            off = mic_offsets[spk]
            a = int(max(0, off) * SR)
            sig = own[a: a + int(total * SR)] if off >= 0 else np.concatenate([0.002 * rng.normal(size=int(-off * SR)), own])[: int(total * SR)]
            path = os.path.join(proj, "마이크", pattern.format(k + 1))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            wavfile.write(path, SR, (np.clip(sig, -1, 1) * 32767).astype(np.int16))
        ref_name = pattern.format(1)
        truth[ref_name] = words

        def cam_audio(off, dur):
            seg = scene[int(off * SR): int((off + dur) * SR)]
            return 0.5 * np.convolve(seg, [0.6, 0.3, 0.1], "same") + 0.01 * rng.normal(size=len(seg))

        if si == 0:
            _video(os.path.join(proj, "카드1", "DCIM", "100CANON", "MVI_9447.MP4"), cam_audio(1.5, 56), 56,
                   created="2026-07-11T01:16:27Z")
            _video(os.path.join(proj, "측면캠", "DCIM", "100CANON", "MVI_0014.MP4"), cam_audio(2.0, 55), 55,
                   created="2026-07-11T01:16:28Z")
            _video(os.path.join(proj, "카드3", "CLIPS001", "A_0001C313A260711_101700EJ_CANON-008.MP4"),
                   cam_audio(1.0, 30), 30, size="384x216", created="2026-07-11T01:17:00Z")
            _video(os.path.join(proj, "카드3", "CLIPS001", "A_0002C313A260711_101730EJ_CANON-008.MP4"),
                   cam_audio(31.0, 27), 27, size="384x216", created="2026-07-11T01:17:30Z")   # 분할 녹화
            _video(os.path.join(proj, "카드4", "CJ_19485.MP4"), cam_audio(0.5, 58), 58, size="256x144")
            _video(os.path.join(proj, "측면캠", "DCIM", "100CANON", "MVI_0015.MP4"),   # 나중에 따로 찍은 인서트
                   0.2 * rng.normal(size=12 * SR), 12, created="2026-07-11T02:40:00Z")
        else:
            _video(os.path.join(proj, "카드1", "DCIM", "100CANON", "MVI_9448.MP4"), cam_audio(1.0, 37), 37)
            _video(os.path.join(proj, "측면캠", "DCIM", "100CANON", "MVI_0016.MP4"), cam_audio(0.5, 38), 38)
    # 잡파일
    for junk in ["카드1/DCIM/100CANON/MVI_9447.THM", "카드1/DCIM/100CANON/._MVI_9447.MP4", "카드3/GOPR0001.LRV",
                 "카드3/CLIPS001/A_0001C313A260711_101700EJ_CANON-008.XML", "측면캠/MISC/info.bin"]:
        path = os.path.join(proj, junk)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"junk")

    tdir = os.path.join(root, "work", "transcript", project)
    os.makedirs(tdir, exist_ok=True)
    for ref_name, words in truth.items():
        label = f"{project}__{os.path.splitext(ref_name)[0]}"
        plain = [{k: w[k] for k in ("w", "s", "e", "p")} for w in words]   # 전사에는 화자 정보가 없다
        with open(os.path.join(tdir, f"{label}.json"), "w", encoding="utf-8") as f:
            json.dump({"language": "ko", "duration": 60.0, "words": plain, "segments": []}, f, ensure_ascii=False)
    return {"project": proj, "work": os.path.join(root, "work"), "output": os.path.join(root, "output"),
            "truth": truth}
