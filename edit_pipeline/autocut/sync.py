"""1단계: 오디오 싱크.

기준 트랙(별도 녹음)과 각 카메라 오디오를 상호상관해 오프셋을 구한다.
- 거친 탐색: 2 kHz 로 내린 전체 신호에 GCC-PHAT(부분 백색화)
- 정밀 보정: 16 kHz 로 30초 창만 다시 상관 → 1ms 이하 정밀도
- 슬레이트(박수) 피크는 결과가 맞는지 검증하는 보조 지표로만 사용

오프셋 정의: 기준 트랙 시각 = 카메라 파일 시각 + offset
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field

import numpy as np

from .media import MediaError, load_audio, probe
from .scan import Place

COARSE_SR = 2000
FINE_SR = 16000


@dataclass
class ClipSync:
    camera: str
    role: str
    file: str
    duration: float = 0.0
    offset: float | None = None
    confidence: float = 0.0     # 0~1, 1 - (두 번째 피크 / 최고 피크)
    zscore: float = 0.0          # 최고 피크가 잡음 대비 몇 σ 인지
    slate: dict | None = None    # 슬레이트 검증 결과
    status: str = "ok"           # ok / failed / missing
    reason: str = ""
    media: dict = field(default_factory=dict)


def _xcorr(ref: np.ndarray, clip: np.ndarray, phat: float = 0.8) -> tuple[np.ndarray, int]:
    """ref 와 clip 의 상호상관. 반환 (cc, zero_index): cc[zero_index + lag] 가 lag 샘플 상관값."""
    n = len(ref) + len(clip) - 1
    nfft = 1 << (n - 1).bit_length()
    fr = np.fft.rfft(ref, nfft)
    fc = np.fft.rfft(clip, nfft)
    spec = fr * np.conj(fc)
    if phat:
        spec /= np.abs(spec) ** phat + 1e-12
    cc = np.fft.irfft(spec, nfft)
    # 음수 lag(-len(clip)+1 .. -1) 는 배열 끝에 있으므로 앞으로 돌린다.
    cc = np.concatenate([cc[nfft - (len(clip) - 1):], cc[: len(ref)]])
    return cc, len(clip) - 1


def _peak_stats(cc: np.ndarray, guard: int) -> tuple[int, float, float]:
    mag = np.abs(cc)
    k = int(np.argmax(mag))
    peak = mag[k]
    masked = mag.copy()
    masked[max(0, k - guard): k + guard + 1] = 0
    second = masked.max() if masked.size else 0.0
    confidence = float(1 - second / peak) if peak > 0 else 0.0
    std = float(masked.std()) or 1e-12
    z = float((peak - masked.mean()) / std)
    return k, confidence, z


def estimate_offset(ref: np.ndarray, clip: np.ndarray, sr: int) -> tuple[float, float, float]:
    """(offset 초, confidence, zscore)."""
    if len(ref) == 0 or len(clip) == 0:
        return 0.0, 0.0, 0.0
    cc, zero = _xcorr(ref, clip)
    k, conf, z = _peak_stats(cc, guard=int(0.05 * sr))
    return (k - zero) / sr, conf, z


def refine_offset(ref_path: str, clip_path: str, coarse: float, clip_dur: float, ref_dur: float,
                  window: float = 30.0, search: float = 0.25) -> float:
    """16 kHz 로 짧은 창만 다시 상관해 오프셋을 정밀화한다."""
    # 겹치는 구간의 가운데쯤에서 창을 잡는다.
    ov_start = max(0.0, -coarse)
    ov_end = min(clip_dur, ref_dur - coarse)
    if ov_end - ov_start < 2.0:
        return coarse
    win = min(window, ov_end - ov_start)
    c_start = ov_start + (ov_end - ov_start - win) / 2
    clip = load_audio(clip_path, FINE_SR, start=c_start, duration=win)
    r_start = c_start + coarse - search
    ref = load_audio(ref_path, FINE_SR, start=max(0.0, r_start), duration=win + 2 * search)
    if len(clip) < FINE_SR or len(ref) < FINE_SR:
        return coarse
    cc, zero = _xcorr(ref, clip, phat=0.8)
    lo = zero
    hi = zero + int(2 * search * FINE_SR) + 1
    seg = np.abs(cc[lo:hi])
    if seg.size == 0:
        return coarse
    k = int(np.argmax(seg))
    return max(0.0, r_start) + k / FINE_SR - c_start


def _transient(sig: np.ndarray, sr: int) -> tuple[float, float] | None:
    """가장 날카로운 타격음 위치와 돋보이는 정도."""
    if len(sig) < sr // 10:
        return None
    d = np.abs(np.diff(sig))
    hop = max(1, sr // 1000)  # 1ms 블록 최대값
    blocks = d[: len(d) // hop * hop].reshape(-1, hop).max(axis=1)
    if blocks.size == 0:
        return None
    k = int(np.argmax(blocks))
    prominence = float(blocks[k] / (np.median(blocks) + 1e-9))
    return k * hop / sr, prominence


def slate_check(ref_path: str, clip_path: str, offset: float, head: float = 60.0,
                tolerance: float = 0.04, min_prominence: float = 20.0) -> dict | None:
    """카메라 앞부분의 박수 피크가 기준 트랙의 예상 위치에도 있는지 확인한다."""
    try:
        clip = load_audio(clip_path, FINE_SR, duration=head)
    except MediaError:
        return None
    hit = _transient(clip, FINE_SR)
    if not hit or hit[1] < min_prominence:
        return {"found": False}
    t_clip, prom = hit
    expected = t_clip + offset
    try:
        ref = load_audio(ref_path, FINE_SR, start=max(0.0, expected - 0.5), duration=1.0)
    except MediaError:
        return {"found": False}
    rhit = _transient(ref, FINE_SR)
    if not rhit:
        return {"found": True, "agree": False}
    t_ref = max(0.0, expected - 0.5) + rhit[0]
    err = t_ref - expected
    return {"found": True, "clip_time": round(t_clip, 3), "error": round(err, 4),
            "agree": bool(abs(err) <= tolerance and rhit[1] >= min_prominence / 2)}


def _probe_safe(path: str) -> tuple[dict | None, str]:
    try:
        info = probe(path)
    except MediaError as exc:
        return None, str(exc)
    if not info.has_audio:
        return info.to_dict(), "오디오 트랙 없음"
    return info.to_dict(), ""


def sync_place(place: Place, rules: dict, log=print) -> dict:
    """장소 하나의 싱크 결과를 JSON 직렬화 가능한 dict 로 돌려준다."""
    min_conf = rules.get("min_confidence", 0.25)
    min_z = rules.get("min_zscore", 8.0)
    warnings: list[str] = []

    # 기준 트랙 결정: 별도 녹음 → 손상/없음이면 정면 카메라
    refs: list[dict] = []
    for path in place.audio_files:
        info, err = _probe_safe(path)
        if err:
            warnings.append(f"기준 오디오 사용 불가({os.path.basename(path)}): {err}")
            continue
        refs.append({"file": path, "duration": info["duration"], "source": "recorder", "media": info})
    if not refs:
        front = next((c for c in place.cameras if c.role == "front" and c.files), None) or \
            next((c for c in place.cameras if c.files), None)
        if front:
            for path in front.files:
                info, err = _probe_safe(path)
                if not err:
                    refs.append({"file": path, "duration": info["duration"], "source": f"camera:{front.name}", "media": info})
            if refs:
                warnings.append(f"기준 트랙을 {front.name} 오디오로 대체")
    if not refs:
        return {"place": place.name, "sessions": [], "warnings": warnings + ["기준 트랙 없음 — 싱크 불가"]}

    coarse_refs = {}
    for ref in refs:
        coarse_refs[ref["file"]] = load_audio(ref["file"], COARSE_SR)

    sessions = {ref["file"]: {"reference": ref, "clips": []} for ref in refs}
    for cam in place.cameras:
        for path in cam.files:
            clip = ClipSync(camera=cam.name, role=cam.role, file=path)
            info, err = _probe_safe(path)
            if info:
                clip.media = info
                clip.duration = info["duration"]
            if err:
                clip.status, clip.reason = "failed", err
                warnings.append(f"{cam.name}/{os.path.basename(path)}: {err}")
                sessions[refs[0]["file"]]["clips"].append(asdict(clip))
                continue
            if any(ref["file"] == path for ref in refs):
                clip.offset, clip.confidence, clip.zscore = 0.0, 1.0, 99.0
                sessions[path]["clips"].append(asdict(clip))
                continue
            sig = load_audio(path, COARSE_SR)
            best = None
            for ref in refs:
                off, conf, z = estimate_offset(coarse_refs[ref["file"]], sig, COARSE_SR)
                if best is None or (conf, z) > (best[1], best[2]):
                    best = (off, conf, z, ref)
            off, conf, z, ref = best
            clip.confidence, clip.zscore = round(conf, 3), round(z, 1)
            if conf < min_conf or z < min_z:
                clip.status = "failed"
                clip.reason = f"신뢰도 미달(confidence={conf:.2f}, z={z:.1f})"
                warnings.append(f"{cam.name}/{os.path.basename(path)}: 싱크 실패 → 제외")
            else:
                clip.offset = round(refine_offset(ref["file"], path, off, clip.duration, ref["duration"]), 4)
                clip.slate = slate_check(ref["file"], path, clip.offset)
                if clip.slate and clip.slate.get("found") and not clip.slate.get("agree"):
                    warnings.append(f"{cam.name}/{os.path.basename(path)}: 슬레이트 피크와 불일치(확인 필요)")
            log(f"  [{place.name}] {cam.name}/{os.path.basename(path)} → "
                f"{'offset %.3fs' % clip.offset if clip.offset is not None else '실패'} (conf {clip.confidence:.2f})")
            sessions[ref["file"]]["clips"].append(asdict(clip))

    failed_cams = []
    for cam in place.cameras:
        clips = [c for s in sessions.values() for c in s["clips"] if c["camera"] == cam.name]
        if not clips or all(c["status"] != "ok" for c in clips):
            failed_cams.append(cam.name)
    return {
        "place": place.name,
        "sessions": [s for s in sessions.values()],
        "excluded_cameras": failed_cams,
        "warnings": warnings,
    }
