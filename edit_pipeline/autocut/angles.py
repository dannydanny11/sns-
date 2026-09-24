"""4단계: 카메라 앵글 배치.

동일 인물을 화각만 달리한 구성이므로 리듬 규칙으로 처리한다.
- 기본은 정면(base)
- 주제 전환 지점 첫 샷은 와이드
- 강조 대목(대본 **표시**)은 망원
- 무음·NG 를 잘라낸 점프컷 자리는 다른 앵글로 덮는다
- 한 앵글이 max_hold 초 이상 이어지면 측면(alt)으로 잠깐 전환
- min_shot 보다 짧은 샷은 앞 샷에 합친다
- 싱크 실패 카메라, 해당 시각을 찍지 않은 카메라는 후보에서 자동 제외
"""
from __future__ import annotations

import os


class CameraPool:
    def __init__(self, session: dict, roles: dict):
        self.clips = [c for c in session["clips"] if c["status"] == "ok" and c["offset"] is not None]
        self.roles = roles

    def cams_for(self, slot: str) -> list[str]:
        """슬롯(base/wide/tele/alt) → 카메라 폴더 목록(존재하는 것만)."""
        wanted = self.roles.get(slot, [])
        if isinstance(wanted, str):
            wanted = [wanted]
        names = []
        for role in wanted:
            for c in self.clips:
                if c["role"] == role and c["camera"] not in names:
                    names.append(c["camera"])
        return names

    def clip_covering(self, camera: str, s: float, e: float) -> dict | None:
        for c in self.clips:
            if c["camera"] == camera and c["offset"] <= s + 1e-3 and c["offset"] + c["duration"] * c.get("rate", 1.0) >= e - 1e-3:
                return c
        return None

    def pick(self, slots: list[str], s: float, e: float, avoid: str | None = None) -> tuple[str, dict] | None:
        """slots 순서대로 [s, e] 를 온전히 찍은 카메라를 찾는다. avoid 는 가능하면 피한다."""
        candidates = []
        for slot in slots:
            for cam in self.cams_for(slot):
                clip = self.clip_covering(cam, s, e)
                if clip and cam not in [x[0] for x in candidates]:
                    candidates.append((cam, clip))
        for cam, clip in candidates:
            if cam != avoid:
                return cam, clip
        if candidates:
            return candidates[0]
        for c in self.clips:  # 최후 수단: 아무 카메라나
            if self.clip_covering(c["camera"], s, e):
                return c["camera"], c
        return None


def resolve_speaker_cams(mapping: dict[str, str], pool: "CameraPool") -> dict[str, str]:
    """{"이형": "C400"} 처럼 화자 → 카메라(이름 또는 파일 이름 일부)를 실제 카메라 이름으로 바꾼다."""
    out = {}
    for spk, key in mapping.items():
        for c in pool.clips:
            if c["camera"] == key or key.lower() in os.path.basename(c["file"]).lower():
                out[spk] = c["camera"]
                break
    return out


def _snap(t: float, bounds: list[float], lo: float, hi: float) -> float:
    inside = [b for b in bounds if lo < b < hi]
    return min(inside, key=lambda b: abs(b - t)) if inside else t


def place_angles(plan: dict, session: dict, rules: dict) -> list[dict]:
    """기준 트랙 시간축 구간 목록 → 샷 목록(아직 시퀀스 위치는 없음)."""
    roles = rules.get("roles", {"base": ["front"], "wide": ["wide"], "tele": ["tele"], "alt": ["side"]})
    min_shot = rules.get("min_shot", 2.0)
    max_hold = rules.get("max_hold", 15.0)
    alt_hold = rules.get("alt_hold", 5.0)
    wide_hold = rules.get("wide_hold", 4.0)
    cover_jumps = rules.get("cover_jump_cuts", True)
    follow_speaker = rules.get("switch_on_speaker", True)
    pool = CameraPool(session, roles)
    speaker_cam = resolve_speaker_cams(rules.get("speaker_cams") or {}, pool)
    words = plan.get("words", [])
    bounds = sorted({w["e"] for w in words} | {w["s"] for w in words})

    # 1) 요청 구간 만들기: (s, e, slots, why)
    requests = []
    prev_e = None
    prev_spk = None
    for it in plan["items"]:
        if not it["enabled"]:
            continue
        spk = it.get("spk")
        spk_change = bool(spk and prev_spk and spk != prev_spk)
        prev_spk = spk or prev_spk
        for k, seg in enumerate(it["segments"]):
            s, e = seg["s"], seg["e"]
            jump = prev_e is not None and abs(s - prev_e) > 0.05
            marks: list[tuple[float, float, list[str], str]] = []
            if k == 0 and it.get("topic_start"):
                we = min(e, s + wide_hold)
                marks.append((s, _snap(we, bounds, s + min_shot, e) if we < e else e, ["wide", "alt"], "주제 전환"))
            for sp in it.get("emphasis", []):
                a, b = max(s, sp["s"] - 0.1), min(e, sp["e"] + 0.2)
                if b - a >= min_shot * 0.75:
                    marks.append((a, b, ["tele"], "강조"))
            marks.sort()
            t = s
            first = True
            for a, b, slots, why in marks:
                if a < t:
                    a = t
                if b <= a:
                    continue
                if a - t < min_shot:  # 앞 자투리가 짧으면 특수 샷을 구간 시작까지 당긴다
                    a = t
                if a - t > 0.01:
                    requests.append({"s": t, "e": a, "slots": None, "why": "", "jump": jump and first})
                    first = False
                requests.append({"s": a, "e": b, "slots": slots, "why": why, "jump": jump and first})
                first = False
                t = b
            if e - t > 0.01:
                requests.append({"s": t, "e": e, "slots": None, "why": "", "jump": jump and first})
            if k == 0:
                for rq in requests[::-1]:
                    if rq["s"] >= s - 1e-6:
                        rq["spk"], rq["spk_change"] = spk, spk_change and rq["s"] == s
                    else:
                        break
            prev_e = e

    # 2) 자동 구간에 기본/측면 배정, 오래 머물면 측면으로 끊기
    shots: list[dict] = []
    hold = 0.0
    cur_auto = "base"
    for rq in requests:
        if rq["slots"]:
            prev_cam = shots[-1]["camera"] if shots and rq["jump"] else None
            got = pool.pick(rq["slots"] + ["base", "alt"], rq["s"], rq["e"], avoid=prev_cam)
            if got:
                shots.append(_shot(rq["s"], rq["e"], got, rq["why"]))
            hold = 0.0
            cur_auto = "base"
            continue
        s = rq["s"]
        want = speaker_cam.get(rq.get("spk"))
        if rq.get("spk_change") and follow_speaker and shots and hold >= min_shot:
            # 화자가 바뀌면 앵글도 바꾼다(셀렉츠 실작업에서 연속 발화 중 컷의 95%가 앵글 전환)
            cur_auto = "base" if want else ("alt" if cur_auto == "base" else "base")
            hold = 0.0
        elif rq["jump"] and cover_jumps and shots and hold >= min_shot:
            cur_auto = "alt" if cur_auto == "base" else "base"
            hold = 0.0
        while s < rq["e"] - 1e-3:
            limit = (alt_hold if cur_auto == "alt" else max_hold) - hold
            end = rq["e"]
            if end - s > limit and limit > 0:
                end = _snap(s + limit, bounds, s + min_shot, rq["e"] - min_shot) if rq["e"] - s > 2 * min_shot else rq["e"]
            prev_cam = shots[-1]["camera"] if shots else None
            slots = ["alt", "base"] if cur_auto == "alt" else ["base", "alt"]
            got = None
            if want and cur_auto == "base":
                clip = pool.clip_covering(want, s, end)
                got = (want, clip) if clip else None
            if not got:
                got = pool.pick(slots, s, end, avoid=prev_cam if cur_auto == "alt" else None)
            if got:
                why = f"화자 {rq.get('spk')}" if want and got[0] == want else ("측면 전환" if cur_auto == "alt" else "")
                shots.append(_shot(s, end, got, why))
            hold += end - s
            if end < rq["e"] - 1e-3:
                cur_auto = "alt" if cur_auto == "base" else "base"
                hold = 0.0
            s = end
    return _merge_short(shots, min_shot)


def _shot(s, e, got, why):
    cam, clip = got
    return {"s": round(s, 3), "e": round(e, 3), "camera": cam, "role": clip["role"], "file": clip["file"],
            "offset": clip["offset"], "rate": clip.get("rate", 1.0), "media": clip.get("media", {}), "why": why}


def _merge_short(shots: list[dict], min_shot: float) -> list[dict]:
    """짧은 샷을 이웃과 합친다. 원본상 이어진(점프 없는) 샷끼리만 카메라를 바꿔 합칠 수 있다."""
    out: list[dict] = []
    for sh in shots:
        if out and out[-1]["camera"] == sh["camera"] and abs(out[-1]["e"] - sh["s"]) < 1e-3:
            out[-1]["e"] = sh["e"]
            continue
        if out and sh["e"] - sh["s"] < min_shot and abs(out[-1]["e"] - sh["s"]) < 1e-3 and not sh["why"]:
            prev = out[-1]
            if prev["offset"] + prev["media"].get("duration", 1e9) * prev["rate"] >= sh["e"]:
                prev["e"] = sh["e"]
                continue
        out.append(dict(sh))
    # 앞 샷이 너무 짧으면 뒤 샷을 앞으로 당긴다(원본 연속 구간일 때만)
    k = 0
    while k < len(out) - 1:
        a, b = out[k], out[k + 1]
        before = out[k - 1] if k > 0 else None
        same_across_jump = before is not None and before["camera"] == b["camera"] and abs(before["e"] - a["s"]) > 1e-3
        if a["e"] - a["s"] < min_shot and abs(a["e"] - b["s"]) < 1e-3 and not a["why"] \
                and b["offset"] <= a["s"] and not same_across_jump:
            b["s"] = a["s"]
            out.pop(k)
            continue
        k += 1
    return out
