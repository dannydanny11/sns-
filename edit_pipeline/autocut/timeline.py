"""샷 목록(기준 트랙 시간축) → 시퀀스 타임라인(프레임 단위).

세션이 여러 개(장소별 폴더, 녹음 파일 분할)면 순서대로 이어 붙인다.
프레임은 기준 시간축에서 반올림하므로 누적 오차가 생기지 않는다.
"""
from __future__ import annotations

import os
from fractions import Fraction


def to_frames(t: float, rate: Fraction) -> int:
    return int(round(t * rate))


def audio_pieces(tracks: list[dict], ref_s: float, ref_e: float, rec_start: int, rate: Fraction,
                 enabled: bool = True) -> list[tuple[str, dict]]:
    """기준 구간 [ref_s, ref_e] 를 각 오디오 트랙(마이크)에서 잘라 온다. 녹음 범위 밖은 잘라 낸다."""
    out = []
    f0 = to_frames(ref_s, rate)
    for t in tracks:
        r = t.get("rate", 1.0)
        a = max(ref_s, t["offset"])
        b = min(ref_e, t["offset"] + t["media"]["duration"] * r)
        fa, fb = to_frames(a, rate), to_frames(b, rate)
        if fb <= fa:
            continue
        src_in = to_frames((a - t["offset"]) / r, rate)
        out.append((t["name"], {"file": t["file"], "media": t["media"], "start": rec_start + fa - f0,
                                "end": rec_start + fb - f0, "in": src_in, "out": src_in + fb - fa,
                                "enabled": enabled}))
    return out


def _primary(pieces, ref, rec, length, f_s, f_e) -> dict:
    """미리보기 재생용 대표 오디오: 이 구간을 녹음한 첫 트랙(분할 녹음이면 해당 파일)."""
    for _, p in pieces:
        if p["start"] == rec:
            return {"file": p["file"], "media": p["media"], "start": rec, "end": rec + length,
                    "in": p["in"], "out": p["in"] + length}
    return {"file": ref["file"], "media": ref["media"], "start": rec, "end": rec + length, "in": f_s, "out": f_e}


def _tracks_of(session: dict) -> list[dict]:
    ref = session["reference"]
    return ref.get("tracks") or [{"file": ref["file"], "offset": 0.0, "rate": 1.0, "media": ref["media"],
                                  "name": "녹음"}]


def video_pieces(clips: list[dict], ref_s: float, ref_e: float, rec_start: int, rate: Fraction,
                 active: str, why: str = "") -> list[tuple[str, dict]]:
    """기준 구간을 찍은 모든 카메라 클립을 잘라 온다(셀렉츠식 멀티캠: 고른 카메라만 켜짐)."""
    out = []
    f0 = to_frames(ref_s, rate)
    for c in clips:
        r = c.get("rate", 1.0)
        a = max(ref_s, c["offset"])
        b = min(ref_e, c["offset"] + c["duration"] * r)
        fa, fb = to_frames(a, rate), to_frames(b, rate)
        if fb <= fa:
            continue
        src_in = to_frames((a - c["offset"]) / r, rate)
        on = c["camera"] == active
        out.append((c["camera"], {"file": c["file"], "camera": c["camera"], "role": c["role"], "media": c["media"],
                                  "start": rec_start + fa - f0, "end": rec_start + fb - f0, "in": src_in,
                                  "out": src_in + fb - fa, "why": why if on else "", "enabled": on}))
    return out


def _stacked(vtracks: dict, atracks: dict, cam_audio: dict) -> dict:
    order = {"front": 0, "side": 1, "wide": 2, "two": 3, "tele": 4}
    vt = sorted(vtracks.items(), key=lambda kv: (order.get(kv[1][0]["role"], 5), kv[0]))
    return {"video_tracks": [cl for _, cl in vt], "video_names": [k for k, _ in vt],
            "audio_tracks": [{"name": k, "clips": v} for k, v in atracks.items()]
            + [{"name": f"{k} 오디오", "clips": v} for k, v in sorted(cam_audio.items())]}


def build_timeline(parts: list[dict], rate: Fraction, stack: bool = True, camera_audio: bool = True) -> dict:
    """parts: [{"session": sync 세션, "plan": 컷 계획, "shots": 샷 목록, "label": str}]

    stack=True 면 셀렉츠처럼 컷마다 모든 카메라를 트랙별로 쌓고 고른 카메라만 켠다(프리미어에서 켜고 끄기로 앵글 교체).
    카메라 오디오는 꺼진 채로 함께 넣고, 마이크(녹음) 트랙만 켠다.
    """
    video, audio, markers, mapping = [], [], [], []
    atracks: dict[str, list[dict]] = {}
    vtracks: dict[str, list[dict]] = {}
    cam_audio: dict[str, list[dict]] = {}
    rec = 0
    for part in parts:
        ref = part["session"]["reference"]
        tracks = _tracks_of(part["session"])
        cams = [c for c in part["session"]["clips"] if c["status"] == "ok" and c["offset"] is not None]
        for sh in part["shots"]:
            f_s, f_e = to_frames(sh["s"], rate), to_frames(sh["e"], rate)
            if f_e <= f_s:
                continue
            length = f_e - f_s
            src_in = to_frames((sh["s"] - sh["offset"]) / sh.get("rate", 1.0), rate) if sh["file"] else 0
            video.append({"file": sh["file"], "camera": sh["camera"] or "(영상 없음)", "role": sh["role"], "media": sh["media"],
                          "start": rec, "end": rec + length, "in": src_in, "out": src_in + length,
                          "why": sh["why"], "enabled": True})
            pieces = audio_pieces(tracks, f_s / rate, f_e / rate, rec, rate)
            audio.append(_primary(pieces, ref, rec, length, f_s, f_e))
            for name, piece in pieces:
                atracks.setdefault(name, []).append(piece)
            if stack:
                for cam, piece in video_pieces(cams, f_s / rate, f_e / rate, rec, rate, sh["camera"], sh["why"]):
                    vtracks.setdefault(cam, []).append(piece)
                    if camera_audio and piece["media"].get("has_audio"):
                        cam_audio.setdefault(cam, []).append({**piece, "enabled": False, "why": ""})
            mapping.append({"ref_s": float(f_s / rate), "ref_e": float(f_e / rate), "rec_s": float(rec / rate), "label": part["label"]})
            rec += length
        for it in part["plan"]["items"]:
            if it["enabled"] and it.get("topic_start") and it["segments"]:
                pos = _rec_of(mapping, it["segments"][0]["s"], part["label"], rate)
                if pos is not None:
                    title = it.get("chapter") or (it.get("script") or it["text"])[:24]
                    markers.append({"frame": pos, "name": title, "comment": part["label"]})
    out = {"video": video, "audio": audio, "markers": markers, "mapping": mapping, "duration": rec,
           "audio_tracks": [{"name": k, "clips": v} for k, v in atracks.items()]}
    if stack and vtracks:
        out.update(_stacked(vtracks, atracks, cam_audio if camera_audio else {}))
    return out


def _rec_of(mapping, ref_t, label, rate):
    t = rec_time(mapping, ref_t, label)
    return None if t is None else int(round(t * rate))


def build_review(parts: list[dict], rate: Fraction, pick_base) -> dict:
    """검토용 시퀀스: 모든 테이크를 원래 순서로 늘어놓고 탈락 테이크는 비활성 클립으로 둔다."""
    video, audio, markers = [], [], []
    atracks: dict[str, list[dict]] = {}
    rec = 0
    for part in parts:
        ref = part["session"]["reference"]
        tracks = _tracks_of(part["session"])
        for it in part["plan"]["items"]:
            s = it["segments"][0]["s"] if it["segments"] else it["s"]
            e = it["segments"][-1]["e"] if it["segments"] else it["e"]
            got = pick_base(part["session"], s, e)
            f_s, f_e = to_frames(s, rate), to_frames(e, rate)
            if not got or f_e <= f_s:
                continue
            clip = got
            length = f_e - f_s
            src_in = to_frames((s - clip["offset"]) / clip.get("rate", 1.0), rate)
            video.append({"file": clip["file"], "camera": clip["camera"], "role": clip["role"],
                          "media": clip.get("media", {}), "start": rec, "end": rec + length,
                          "in": src_in, "out": src_in + length, "why": it["reason"], "enabled": it["enabled"]})
            pieces = audio_pieces(tracks, f_s / rate, f_e / rate, rec, rate, it["enabled"])
            audio.append({**_primary(pieces, ref, rec, length, f_s, f_e), "enabled": it["enabled"]})
            for name, piece in pieces:
                atracks.setdefault(name, []).append(piece)
            if not it["enabled"]:
                markers.append({"frame": rec, "name": it["reason"], "comment": it["text"][:80]})
            rec += length
    return {"video": video, "audio": audio, "markers": markers, "mapping": [], "duration": rec,
            "audio_tracks": [{"name": k, "clips": v} for k, v in atracks.items()]}


def build_sync_timeline(syncs: list[dict], rate: Fraction, gap: float = 2.0) -> dict:
    """셀렉츠의 '싱크 타임라인': 촬영 전체를 기준 녹음 시간축에 펼친 멀티캠 시퀀스.

    카메라마다 비디오 트랙 하나(+ 카메라 오디오 트랙), 마이크마다 오디오 트랙 하나.
    세션(녹음 파일)은 촬영 순서대로 gap 초 간격을 두고 이어 붙이고, 세션 시작에 마커를 둔다.
    싱크가 안 된 인서트 영상은 맨 뒤에 별도 트랙으로 늘어놓는다.
    """
    vtracks: dict[str, list[dict]] = {}
    atracks: dict[str, list[dict]] = {}
    markers = []
    rec = 0
    sessions = [(sy, s) for sy in syncs for s in sy["sessions"]]
    for sy, sess in sessions:
        ok = [c for c in sess["clips"] if c["status"] == "ok" and c["offset"] is not None]
        tracks = _tracks_of(sess)
        lo = min([0.0] + [c["offset"] for c in ok] + [t["offset"] for t in tracks])
        hi = max([sess["reference"]["duration"]] + [c["offset"] + c["duration"] * c.get("rate", 1.0) for c in ok]
                 + [t["offset"] + t["media"]["duration"] * t.get("rate", 1.0) for t in tracks])
        base = rec - to_frames(lo, rate)
        markers.append({"frame": rec, "name": os.path.basename(sess["reference"]["file"]), "comment": sy["place"]})
        for c in sorted(ok, key=lambda c: (c["camera"], c["offset"])):
            start = base + to_frames(c["offset"], rate)
            length = to_frames(c["duration"], rate)
            vtracks.setdefault(c["camera"], []).append({
                "file": c["file"], "camera": c["camera"], "role": c["role"], "media": c["media"],
                "start": start, "end": start + length, "in": 0, "out": length, "why": "", "enabled": True})
            if c["media"].get("has_audio"):
                atracks.setdefault(f"{c['camera']} 오디오", []).append({
                    "file": c["file"], "media": c["media"], "start": start, "end": start + length,
                    "in": 0, "out": length, "enabled": True})
        for t in tracks:
            start = base + to_frames(t["offset"], rate)
            length = to_frames(t["media"]["duration"], rate)
            atracks.setdefault(t["name"], []).append({"file": t["file"], "media": t["media"], "start": start,
                                                      "end": start + length, "in": 0, "out": length, "enabled": True})
        rec = base + to_frames(hi, rate) + to_frames(gap, rate)
    inserts = [c for sy in syncs for c in sy.get("inserts", []) if c.get("media", {}).get("has_video")]
    if inserts:
        markers.append({"frame": rec, "name": "인서트(싱크 안 된 영상)", "comment": ""})
        for c in inserts:
            length = to_frames(c["duration"], rate)
            vtracks.setdefault("인서트", []).append({
                "file": c["file"], "camera": "인서트", "role": "insert", "media": c["media"], "start": rec,
                "end": rec + length, "in": 0, "out": length, "why": "", "enabled": True})
            rec += length
    # 트랙 순서: 인서트가 맨 위, 정면이 맨 아래(프리미어 V1)
    order = {"front": 0, "side": 1, "wide": 2, "two": 3, "tele": 4, "insert": 9}
    vt = sorted(vtracks.items(), key=lambda kv: (order.get(kv[1][0]["role"], 5), kv[0]))
    at = sorted(atracks.items(), key=lambda kv: (kv[0].endswith(" 오디오"), kv[0]))
    return {"video": [c for _, cl in vt for c in cl], "video_tracks": [cl for _, cl in vt],
            "video_names": [k for k, _ in vt], "audio": [], "markers": markers, "mapping": [],
            "duration": max([rec] + [c["end"] for _, cl in vt for c in cl]),
            "audio_tracks": [{"name": k, "clips": v} for k, v in at]}


def rec_time(mapping: list[dict], ref_t: float, label: str, tol: float = 0.05) -> float | None:
    """기준 시각 → 시퀀스 시각(초). 잘려나간 구간이면 None. 프레임 반올림 오차(tol)는 허용."""
    for m in mapping:
        if m["label"] == label and m["ref_s"] - tol <= ref_t <= m["ref_e"] + tol:
            return m["rec_s"] + min(max(ref_t, m["ref_s"]), m["ref_e"]) - m["ref_s"]
    return None
