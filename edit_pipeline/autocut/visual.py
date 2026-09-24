"""카메라 역할 자동 판정(얼굴 분석).

카메라마다 몇 장면을 뽑아 OpenCV 기본 얼굴 검출기로
정면 얼굴 비율, 옆얼굴 비율, 얼굴 크기, 인원 수를 잰다.
- 2인 구도: 한 화면에 얼굴이 둘 이상인 경우가 많음
- 정면: 정면 얼굴이 가장 꾸준히 잡힘
- 망원: 얼굴이 정면 카메라보다 훨씬 큼 / 와이드: 훨씬 작음
- 측면: 옆얼굴이 정면 얼굴보다 많이 잡힘, 또는 나머지
OpenCV 가 없거나 얼굴이 안 잡히면 촬영 분량이 가장 긴 카메라를 정면, 나머지를 측면으로 둔다.
"""
from __future__ import annotations

import subprocess

import numpy as np

from .media import ffmpeg_exe

SAMPLES = 8


def _frame(path: str, t: float):
    import cv2
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(t, 0):.2f}", "-i", path,
           "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "bmp", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    if not out:
        return None
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_GRAYSCALE)


def face_stats(clips: list[dict]) -> dict | None:
    try:
        import cv2
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier"):   # OpenCV 5 부터 기본 패키지에서 빠짐 → 4.x 필요
        return None
    frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    total = sum(c["duration"] for c in clips) or 1
    stats = {"frames": 0, "frontal": 0, "profile": 0, "sizes": [], "counts": []}
    for k in range(SAMPLES):
        pos = total * (k + 0.5) / SAMPLES
        for c in clips:
            if pos <= c["duration"]:
                img = _frame(c["file"], pos)
                break
            pos -= c["duration"]
        else:
            continue
        if img is None:
            continue
        img = cv2.equalizeHist(img)
        h, w = img.shape[:2]
        minsz = (max(20, w // 30), max(20, w // 30))
        fr = frontal.detectMultiScale(img, 1.1, 6, minSize=minsz)
        pr = list(profile.detectMultiScale(img, 1.1, 6, minSize=minsz))
        pr += list(profile.detectMultiScale(cv2.flip(img, 1), 1.1, 6, minSize=minsz))
        stats["frames"] += 1
        faces = list(fr) + pr
        if len(fr):
            stats["frontal"] += 1
        if pr and not len(fr):
            stats["profile"] += 1
        if faces:
            stats["sizes"].append(max(fw * fh for (_, _, fw, fh) in faces) / (w * h))
            stats["counts"].append(max(len(fr), len(pr)))
    n = stats["frames"] or 1
    return {"frontal_rate": stats["frontal"] / n, "profile_rate": stats["profile"] / n,
            "face_rate": len(stats["sizes"]) / n,
            "size": float(np.median(stats["sizes"])) if stats["sizes"] else 0.0,
            "people": float(np.mean(stats["counts"])) if stats["counts"] else 0.0}


def decide(cams: list[dict]) -> None:
    """cams: [{"name", "role"(없으면 None), "coverage", "faces"(face_stats 또는 None)}] — role·evidence 채움."""
    free = [c for c in cams if not c.get("role")]
    has = lambda c: c.get("faces") and c["faces"]["face_rate"] >= 0.3  # noqa: E731
    for c in free:
        f = c.get("faces")
        if has(c) and f["people"] >= 1.6:
            c["role"], c["evidence"] = "two", f"얼굴 분석: 평균 {f['people']:.1f}명"
    taken = {c["role"] for c in cams if c.get("role")}
    free = [c for c in cams if not c.get("role")]
    if "front" not in taken and free:
        faced = [c for c in free if has(c) and c["faces"]["frontal_rate"] >= 0.3]
        if faced:
            front = max(faced, key=lambda c: (c["faces"]["frontal_rate"] - 0.5 * c["faces"]["profile_rate"], c["coverage"]))
            f = front["faces"]
            front["role"], front["evidence"] = "front", f"얼굴 분석: 정면 얼굴 {f['frontal_rate']:.0%}"
        else:
            front = max(free, key=lambda c: c["coverage"])
            front["role"], front["evidence"] = "front", "촬영 분량이 가장 긴 카메라"
    front = next((c for c in cams if c.get("role") == "front"), None)
    ref = front["faces"]["size"] if front and has(front) else None
    for c in cams:
        if c.get("role"):
            continue
        f = c.get("faces")
        if has(c) and ref:
            if f["size"] >= 1.8 * ref:
                c["role"], c["evidence"] = "tele", f"얼굴 분석: 얼굴 크기 정면의 {f['size'] / ref:.1f}배"
            elif f["size"] <= 0.55 * ref:
                c["role"], c["evidence"] = "wide", f"얼굴 분석: 얼굴 크기 정면의 {f['size'] / ref:.1f}배"
            else:
                c["role"] = "side"
                c["evidence"] = "얼굴 분석: 옆얼굴 위주" if f["profile_rate"] > f["frontal_rate"] else "얼굴 분석: 정면과 비슷한 크기"
        elif f and f["face_rate"] < 0.3 and ref:
            c["role"], c["evidence"] = "wide", "얼굴 분석: 얼굴이 거의 안 잡힘(먼 화각 추정)"
        else:
            c["role"], c["evidence"] = "side", "기본값(얼굴 분석 불가)"


def assign_roles(cams_info: list[dict], rules: dict, log=print) -> None:
    need = [ci for ci in cams_info if not ci.get("role")]
    if not need:
        return
    if len(cams_info) == 1:
        cams_info[0]["role"], cams_info[0]["evidence"] = "front", "카메라 1대"
        return
    if rules.get("face_analysis", True):
        for ci in need:
            ci["faces"] = face_stats(ci["clips"])
        if all(ci.get("faces") is None for ci in need):
            log('  (얼굴 분석 생략: pip install "opencv-python-headless<5" 필요)')
    decide(cams_info)
