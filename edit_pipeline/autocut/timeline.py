"""샷 목록(기준 트랙 시간축) → 시퀀스 타임라인(프레임 단위).

세션이 여러 개(장소별 폴더, 녹음 파일 분할)면 순서대로 이어 붙인다.
프레임은 기준 시간축에서 반올림하므로 누적 오차가 생기지 않는다.
"""
from __future__ import annotations

from fractions import Fraction


def to_frames(t: float, rate: Fraction) -> int:
    return int(round(t * rate))


def build_timeline(parts: list[dict], rate: Fraction) -> dict:
    """parts: [{"session": sync 세션, "plan": 컷 계획, "shots": 샷 목록, "label": str}]"""
    video, audio, markers, mapping = [], [], [], []
    rec = 0
    for part in parts:
        ref = part["session"]["reference"]
        for sh in part["shots"]:
            f_s, f_e = to_frames(sh["s"], rate), to_frames(sh["e"], rate)
            if f_e <= f_s:
                continue
            length = f_e - f_s
            src_in = to_frames(sh["s"] - sh["offset"], rate)
            video.append({"file": sh["file"], "camera": sh["camera"], "role": sh["role"], "media": sh["media"],
                          "start": rec, "end": rec + length, "in": src_in, "out": src_in + length,
                          "why": sh["why"], "enabled": True})
            audio.append({"file": ref["file"], "media": ref["media"], "start": rec, "end": rec + length,
                          "in": f_s, "out": f_e})
            mapping.append({"ref_s": float(f_s / rate), "ref_e": float(f_e / rate), "rec_s": float(rec / rate), "label": part["label"]})
            rec += length
        for it in part["plan"]["items"]:
            if it["enabled"] and it.get("topic_start") and it["segments"]:
                pos = _rec_of(mapping, it["segments"][0]["s"], part["label"], rate)
                if pos is not None:
                    title = it.get("chapter") or (it.get("script") or it["text"])[:24]
                    markers.append({"frame": pos, "name": title, "comment": part["label"]})
    return {"video": video, "audio": audio, "markers": markers, "mapping": mapping, "duration": rec}


def _rec_of(mapping, ref_t, label, rate):
    t = rec_time(mapping, ref_t, label)
    return None if t is None else int(round(t * rate))


def build_review(parts: list[dict], rate: Fraction, pick_base) -> dict:
    """검토용 시퀀스: 모든 테이크를 원래 순서로 늘어놓고 탈락 테이크는 비활성 클립으로 둔다."""
    video, audio, markers = [], [], []
    rec = 0
    for part in parts:
        ref = part["session"]["reference"]
        for it in part["plan"]["items"]:
            s = it["segments"][0]["s"] if it["segments"] else it["s"]
            e = it["segments"][-1]["e"] if it["segments"] else it["e"]
            got = pick_base(part["session"], s, e)
            f_s, f_e = to_frames(s, rate), to_frames(e, rate)
            if not got or f_e <= f_s:
                continue
            clip = got
            length = f_e - f_s
            src_in = to_frames(s - clip["offset"], rate)
            video.append({"file": clip["file"], "camera": clip["camera"], "role": clip["role"],
                          "media": clip.get("media", {}), "start": rec, "end": rec + length,
                          "in": src_in, "out": src_in + length, "why": it["reason"], "enabled": it["enabled"]})
            audio.append({"file": ref["file"], "media": ref["media"], "start": rec, "end": rec + length,
                          "in": f_s, "out": f_e, "enabled": it["enabled"]})
            if not it["enabled"]:
                markers.append({"frame": rec, "name": it["reason"], "comment": it["text"][:80]})
            rec += length
    return {"video": video, "audio": audio, "markers": markers, "mapping": [], "duration": rec}


def rec_time(mapping: list[dict], ref_t: float, label: str, tol: float = 0.05) -> float | None:
    """기준 시각 → 시퀀스 시각(초). 잘려나간 구간이면 None. 프레임 반올림 오차(tol)는 허용."""
    for m in mapping:
        if m["label"] == label and m["ref_s"] - tol <= ref_t <= m["ref_e"] + tol:
            return m["rec_s"] + min(max(ref_t, m["ref_s"]), m["ref_e"]) - m["ref_s"]
    return None
