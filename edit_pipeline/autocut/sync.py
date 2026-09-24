"""1단계: 오디오 싱크.

기준 트랙(별도 녹음)과 각 카메라 오디오를 상호상관해 오프셋을 구한다.
- 거친 탐색: 2 kHz 로 내린 전체 신호에 GCC-PHAT(부분 백색화)
- 정밀 보정: 16 kHz 로 30초 창만 다시 상관 → 1ms 이하 정밀도
- 슬레이트(박수) 피크는 결과가 맞는지 검증하는 보조 지표로만 사용

오프셋 정의: 기준 트랙 시각 = 카메라 파일 시각 + offset
"""
from __future__ import annotations

import os
import re
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
    offset: float | None = None  # 카메라 0초가 기준 트랙의 몇 초인지
    rate: float = 1.0            # 시계 오차 보정: 기준 시각 = offset + rate × 카메라 시각
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


def refine_at(ref_path: str, clip_path: str, coarse: float, c_start: float, win: float,
              search: float = 0.25) -> tuple[float, float] | None:
    """카메라 [c_start, c_start+win] 창을 16 kHz 로 다시 상관해 (정밀 오프셋, 선명도) 를 구한다."""
    clip = load_audio(clip_path, FINE_SR, start=c_start, duration=win)
    r_start = c_start + coarse - search
    ref = load_audio(ref_path, FINE_SR, start=max(0.0, r_start), duration=win + 2 * search)
    if len(clip) < FINE_SR or len(ref) < FINE_SR:
        return None
    cc, zero = _xcorr(ref, clip, phat=0.8)
    seg = np.abs(cc[zero: zero + int(2 * search * FINE_SR) + 1])
    if seg.size == 0:
        return None
    k = int(np.argmax(seg))
    sharp = float(seg[k] / (np.median(seg) + 1e-12))
    return max(0.0, r_start) + k / FINE_SR - c_start, sharp


def refine_offset(ref_path: str, clip_path: str, coarse: float, clip_dur: float, ref_dur: float,
                  window: float = 30.0, drift_min: float = 300.0) -> tuple[float, float]:
    """(offset, rate). 겹치는 구간이 drift_min 초 이상이면 여러 지점을 재서 시계 오차(드리프트)를 구한다."""
    ov_start = max(0.0, -coarse)
    ov_end = min(clip_dur, ref_dur - coarse)
    if ov_end - ov_start < 2.0:
        return coarse, 1.0
    win = min(window, ov_end - ov_start)
    span = ov_end - ov_start - win
    fracs = [0.1, 0.3, 0.5, 0.7, 0.9] if ov_end - ov_start >= drift_min else [0.5]
    pts = []
    for f in fracs:
        c0 = ov_start + span * f
        got = refine_at(ref_path, clip_path, coarse, c0, win)
        if got and got[1] > 3.0:
            pts.append((c0 + win / 2, got[0]))
    if not pts:
        return coarse, 1.0
    if len(pts) < 3:
        return float(np.median([o for _, o in pts])), 1.0
    c = np.array([p[0] for p in pts])
    o = np.array([p[1] for p in pts])
    b, a = np.polyfit(c, o, 1)
    resid = np.abs(o - (a + b * c)).max()
    if resid < 0.01 and abs(b) < 5e-4:          # 선형으로 잘 맞고 500ppm 이내일 때만 채택
        if abs(b) * (ov_end - ov_start) < 0.004:  # 전체 오차가 4ms 미만이면 무시
            return float(np.median(o)), 1.0
        return float(a), float(1.0 + b)
    return float(np.median(o)), 1.0


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
                tolerance: float = 0.04, min_prominence: float = 40.0) -> dict | None:
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
    if not rhit or rhit[1] < min_prominence:
        return {"found": False}   # 기준 녹음 쪽에 뚜렷한 박수가 없으면 검증 불가(말소리 시작을 박수로 오인 방지)
    t_ref = max(0.0, expected - 0.5) + rhit[0]
    err = t_ref - expected
    return {"found": True, "clip_time": round(t_clip, 3), "error": round(err, 4), "agree": bool(abs(err) <= tolerance)}


def _probe_safe(path: str) -> tuple[dict | None, str]:
    try:
        info = probe(path)
    except MediaError as exc:
        return None, str(exc)
    if not info.has_audio:
        return info.to_dict(), "오디오 트랙 없음"
    return info.to_dict(), ""


def sync_file(path: str, refs: list[dict], coarse_refs: dict, rules: dict,
              camera: str = "", role: str = "") -> tuple[ClipSync, dict | None]:
    """파일 하나를 모든 기준 트랙과 상관해 가장 잘 맞는 기준과 오프셋을 찾는다."""
    clip = ClipSync(camera=camera, role=role, file=path)
    info, err = _probe_safe(path)
    if info:
        clip.media = info
        clip.duration = info["duration"]
    if err:
        clip.status, clip.reason = "failed", err
        return clip, None
    for ref in refs:
        if ref["file"] == path:
            clip.offset, clip.confidence, clip.zscore = 0.0, 1.0, 99.0
            return clip, ref
    sig = load_audio(path, COARSE_SR)
    best = None
    for ref in refs:
        off, conf, z = estimate_offset(coarse_refs[ref["file"]], sig, COARSE_SR)
        if best is None or (conf, z) > (best[1], best[2]):
            best = (off, conf, z, ref)
    if best is None:
        clip.status, clip.reason = "failed", "기준 트랙 없음"
        return clip, None
    off, conf, z, ref = best
    clip.confidence, clip.zscore = round(conf, 3), round(z, 1)
    if conf < rules.get("min_confidence", 0.25) or z < rules.get("min_zscore", 8.0):
        clip.status = "failed"
        clip.reason = f"녹음과 소리가 맞지 않음(confidence={conf:.2f})"
        return clip, None
    offset, rate = refine_offset(ref["file"], path, off, clip.duration, ref["duration"])
    clip.offset, clip.rate = round(offset, 4), round(rate, 8)
    clip.slate = slate_check(ref["file"], path, clip.offset)
    return clip, ref


def track_name(path: str) -> str:
    """오디오 트랙 표시 이름: TX01_MIC028_… → TX01, ZOOM0001_Tr1 → Tr1, 그 외는 파일 이름."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for pat in (r"^(TX\d+)", r"_(Tr\d+|LR|TrMic|Mix)$", r"^(RX\d+)", r"^(MIC\d+)"):
        m = re.search(pat, stem, re.I)
        if m:
            return m.group(1)
    return stem[:24]


def choose_references(candidates: list[str], rules: dict, source: str, log=print,
                      bundle: bool = True) -> tuple[list[dict], list[str]]:
    """기준 후보(긴 것부터) 중 같은 순간을 녹음한 파일은 한 묶음으로 만든다.

    묶음의 첫 파일이 싱크 기준이고, 나머지(무선 마이크 여러 개, 녹음기 트랙들)는
    기준에 맞춘 오프셋과 함께 reference["tracks"] 에 들어가 각자 오디오 트랙이 된다.
    bundle=False(카메라 오디오를 기준으로 쓸 때)면 겹치는 파일은 그냥 기준 후보에서 뺀다.
    """
    warnings: list[str] = []
    infos = []
    for path in candidates:
        info, err = _probe_safe(path)
        if err:
            warnings.append(f"{os.path.basename(path)}: {err}")
            continue
        infos.append((path, info))

    def pref(item):
        path, info = item
        name = os.path.basename(path).lower()
        mix = any(k in name for k in ("_lr", "mix", "main", "master"))
        return (-round(info["duration"]), not mix, -(info.get("audio_channels") or 1), path)

    refs, coarse = [], {}
    min_conf = max(0.3, rules.get("min_confidence", 0.25))
    for path, info in sorted(infos, key=pref):
        sig = load_audio(path, COARSE_SR)
        hit = None
        for ref in refs:
            off, conf, z = estimate_offset(coarse[ref["file"]], sig, COARSE_SR)
            if conf >= min_conf and z >= rules.get("min_zscore", 8.0):
                hit = (ref, off)
                break
        if hit:
            ref, off = hit
            if bundle:
                off, rate = refine_offset(ref["file"], path, off, info["duration"], ref["duration"])
                ref["tracks"].append({"file": path, "offset": round(off, 4), "rate": round(rate, 8),
                                      "media": info, "name": track_name(path)})
                log(f"  {os.path.basename(path)}: {os.path.basename(ref['file'])} 와 같은 순간 → 오디오 트랙 추가"
                    f" ({off:+.3f}s)")
            continue
        refs.append({"file": path, "duration": info["duration"], "source": source, "media": info,
                     "tracks": [{"file": path, "offset": 0.0, "rate": 1.0, "media": info, "name": track_name(path)}]})
        coarse[path] = sig
    for ref in refs:
        names = [t["name"] for t in ref["tracks"]]
        for t in ref["tracks"]:
            if names.count(t["name"]) > 1:
                t["name"] = os.path.splitext(os.path.basename(t["file"]))[0][:24]
    return refs, warnings


def load_coarse(refs: list[dict]) -> dict:
    return {ref["file"]: load_audio(ref["file"], COARSE_SR) for ref in refs}


def sync_place(place: Place, rules: dict, log=print) -> dict:
    """카메라 폴더(cam_*)가 정리된 경우: 장소 하나의 싱크 결과."""
    warnings: list[str] = []
    refs, w = choose_references(place.audio_files, rules, "recorder", log)
    warnings += w
    if not refs:
        front = next((c for c in place.cameras if c.role == "front" and c.files), None) or \
            next((c for c in place.cameras if c.files), None)
        if front:
            refs, _ = choose_references(front.files, rules, f"camera:{front.name}", log, bundle=False)
            if refs:
                warnings.append(f"기준 트랙을 {front.name} 오디오로 대체")
    if not refs:
        return {"place": place.name, "sessions": [], "warnings": warnings + ["기준 트랙 없음 — 싱크 불가"]}

    coarse_refs = load_coarse(refs)
    sessions = {ref["file"]: {"reference": ref, "clips": []} for ref in refs}
    for cam in place.cameras:
        for path in cam.files:
            clip, ref = sync_file(path, refs, coarse_refs, rules, cam.name, cam.role)
            if clip.status != "ok":
                warnings.append(f"{cam.name}/{os.path.basename(path)}: {clip.reason} → 제외")
            elif clip.slate and clip.slate.get("found") and not clip.slate.get("agree"):
                warnings.append(f"{cam.name}/{os.path.basename(path)}: 슬레이트 피크와 불일치(확인 필요)")
            log(f"  [{place.name}] {cam.name}/{os.path.basename(path)} → "
                f"{'offset %.3fs' % clip.offset if clip.offset is not None else '실패'} (conf {clip.confidence:.2f})")
            sessions[(ref or refs[0])["file"]]["clips"].append(asdict(clip))

    failed_cams = []
    for cam in place.cameras:
        clips = [c for s in sessions.values() for c in s["clips"] if c["camera"] == cam.name]
        if not clips or all(c["status"] != "ok" for c in clips):
            failed_cams.append(cam.name)
    return {"place": place.name, "sessions": list(sessions.values()), "excluded_cameras": failed_cams,
            "warnings": warnings}
