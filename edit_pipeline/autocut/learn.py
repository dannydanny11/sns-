"""편집 스타일 학습: 셀렉츠·프리미어에서 실제로 편집한 XML(FCP7 xmeml)을 읽어 규칙을 뽑는다.

설계 문서의 '셀렉츠 역설계' 단계. 뽑는 것:
- 시퀀스 설정(프레임레이트·해상도)
- 카메라별 사용 비율, 한 번에 머무는 시간(평균·중앙·90%) → 기본/측면/와이드(컷어웨이) 역할과 max_hold 등
- 컷 지점 성격: 내용을 잘라낸 점프컷 vs 이어지는 발화 중 앵글만 바꾼 컷, 각각에서 앵글이 바뀐 비율
- 잘라낸 길이 분포(짧은 쉼 제거 vs 큰 덩어리 삭제), 원본 대비 남긴 비율
- 켜 둔 오디오(개인 마이크) / 꺼 둔 오디오(카메라)
- 마이크 파일이 이 PC 에 있으면: 구간마다 가장 크게 들어온 마이크(화자)와 켜진 카메라를 짝지어 화자 → 카메라

결과 JSON 을 `run --style <파일>` 로 넘기면 같은 스타일로 편집한다.
"""
from __future__ import annotations

import json
import os
import re
import statistics as st
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from fractions import Fraction
from urllib.parse import unquote, urlparse

from .sync import track_name


def _rate(el) -> Fraction:
    tb = int(el.findtext("rate/timebase") or 30)
    ntsc = (el.findtext("rate/ntsc") or "FALSE").upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[_\s-]+", os.path.splitext(os.path.basename(name.replace("\\", "/")))[0]) if t]


def camera_keys(names: list[str]) -> dict[str, str]:
    """파일 이름에서 카메라를 가르는 부분만 남긴다: 20260804_PD은비_R3.MP4 → R3."""
    toks = {n: _tokens(n) for n in names}
    common = set.intersection(*[set(t) for t in toks.values()]) if len(toks) > 1 else set()
    out = {}
    for n, t in toks.items():
        d = [x for x in t if x not in common and not x.isdigit()]
        out[n] = "_".join(d[:2]) if d else os.path.splitext(os.path.basename(n))[0]
    return out


def parse(path: str) -> dict:
    root = ET.parse(path).getroot()
    seq = root.find(".//sequence")
    rate = _rate(seq)
    files: dict[str, dict] = {}
    for f in root.iter("file"):
        if f.find("name") is not None:
            files[f.get("id")] = {"name": f.findtext("name"), "pathurl": f.findtext("pathurl") or "",
                                  "rate": _rate(f)}
    fmt = seq.find("media/video/format/samplecharacteristics")

    def clips(kind):
        out = []
        for ti, t in enumerate(seq.find(f"media/{kind}").findall("track")):
            for c in t.findall("clipitem"):
                f = c.find("file")
                if f is None:
                    continue
                out.append({"track": ti, "file": files[f.get("id")]["name"], "fid": f.get("id"),
                            "start": int(c.findtext("start")), "end": int(c.findtext("end")),
                            "in": int(c.findtext("in")), "out": int(c.findtext("out")),
                            "enabled": (c.findtext("enabled") or "TRUE").upper() == "TRUE"})
        return out

    return {"name": seq.findtext("name"), "rate": rate, "duration": int(seq.findtext("duration") or 0),
            "width": int(fmt.findtext("width")) if fmt is not None and fmt.findtext("width") else None,
            "height": int(fmt.findtext("height")) if fmt is not None and fmt.findtext("height") else None,
            "files": files, "video": clips("video"), "audio": clips("audio")}


def _local(pathurl: str, name: str, media_dir: str | None) -> str | None:
    """XML 에 적힌 원본 경로(또는 media_dir 안의 같은 이름 파일)가 이 PC 에 있으면 그 경로."""
    cands = []
    if pathurl:
        p = unquote(urlparse(pathurl).path)
        if re.match(r"^/[A-Za-z]:/", p):
            p = p[1:]
        cands.append(p)
    if media_dir:
        base = os.path.basename(name.replace("\\", "/"))
        for rootdir, _, fs in os.walk(media_dir):
            if base in fs:
                cands.append(os.path.join(rootdir, base))
    return next((c for c in cands if os.path.exists(c)), None)


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def analyze(path: str, media_dir: str | None = None, log=print) -> dict:
    x = parse(path)
    R = float(x["rate"])
    vid = [c for c in x["video"]]
    keys = camera_keys(sorted({c["file"] for c in vid}))
    for c in vid:
        c["cam"] = keys[c["file"]]

    # 컷(세그먼트) = 켜진 비디오 클립의 [start, end)
    active = sorted((c for c in vid if c["enabled"]), key=lambda c: c["start"])
    segs = [{"s": c["start"], "e": c["end"], "cam": c["cam"], "in": c["in"], "file": c["file"]} for c in active]
    stacked = len({c["track"] for c in vid}) > 1 and len(vid) > len(active)

    # 원본 연속성: 같은 카메라 기준으로 보면 오프셋이 달라 헷갈리므로, 마이크(켜진 오디오) 첫 트랙 기준으로 본다
    aud_on = [c for c in x["audio"] if c["enabled"]]
    first_track = min((c["track"] for c in aud_on), default=None)
    ref = sorted((c for c in aud_on if c["track"] == first_track), key=lambda c: c["start"])

    def ref_in(start):
        for c in ref:
            if c["start"] <= start < c["end"]:
                return c["file"], c["in"] + start - c["start"]
        return None, None

    jumps = cont = sw_jump = sw_cont = 0
    gaps = []
    for a, b in zip(segs, segs[1:]):
        fa, ia = ref_in(a["s"])
        fb, ib = ref_in(b["s"])
        if fa is None or fb is None:
            continue
        gap = (ib - (ia + a["e"] - a["s"])) / R if fa == fb else None
        switched = a["cam"] != b["cam"]
        if gap is not None and abs(gap) < 1 / R + 1e-9:
            cont += 1
            sw_cont += switched
        else:
            jumps += 1
            sw_jump += switched
            if gap is not None:
                gaps.append(gap)

    runs: list[tuple[str, float]] = []
    for sgm in segs:
        d = (sgm["e"] - sgm["s"]) / R
        if runs and runs[-1][0] == sgm["cam"]:
            runs[-1] = (sgm["cam"], runs[-1][1] + d)
        else:
            runs.append((sgm["cam"], d))
    per_cam = defaultdict(list)
    for cam, d in runs:
        per_cam[cam].append(d)
    total = sum(d for _, d in runs) or 1
    cams = {cam: {"share": round(sum(v) / total, 3), "shots": len(v), "mean": round(st.mean(v), 2),
                  "median": round(st.median(v), 2), "p90": round(_q(v, 0.9), 2), "max": round(max(v), 2)}
            for cam, v in per_cam.items()}

    # 역할 추정: 가장 많이 쓴 카메라 = 기본(정면), 가장 짧게 끊어 쓰는 카메라 = 컷어웨이(와이드)
    order = sorted(cams, key=lambda k: -cams[k]["share"])
    roles = {}
    if order:
        roles[order[0]] = "front"
        rest = order[1:]
        if len(rest) >= 2:
            cut = min(rest, key=lambda k: (cams[k]["median"] + cams[k]["mean"], cams[k]["share"]))
            roles[cut] = "wide"
            rest = [k for k in rest if k != cut]
        for k in rest:
            roles[k] = "side"

    audio_on = sorted({track_name(c["file"]) for c in x["audio"] if c["enabled"]})
    audio_off_cams = sorted({keys.get(c["file"], c["file"]) for c in x["audio"] if not c["enabled"]} & set(keys.values()))

    main = cams[order[0]] if order else {"p90": 15, "median": 7}
    alts = [cams[k] for k in order[1:] if roles.get(k) == "side"] or [main]
    wide = [cams[k] for k in order if roles.get(k) == "wide"]
    short_cut = sum(1 for g in gaps if 0 < g < 1.5)
    rules = {
        "max_hold": round(main["p90"], 1),
        "alt_hold": round(st.median([a["median"] for a in alts]), 1),
        "min_shot": round(max(0.8, min(_q([d for _, d in runs], 0.1), 2.0)), 1),
        "multicam_stack": stacked,
        "camera_audio_tracks": bool(audio_off_cams),
        "cover_jump_cuts": jumps == 0 or sw_jump / jumps >= 0.5,
        "switch_on_speaker": cont == 0 or sw_cont / cont >= 0.5,
        "camera_roles": roles,
        "sequence_fps": round(R, 3), "sequence_width": x["width"], "sequence_height": x["height"],
    }
    if wide:
        rules["wide_hold"] = round(wide[0]["median"], 1)

    stats = {
        "sequence": x["name"], "duration_sec": round(x["duration"] / R, 1), "cuts": len(segs), "shots": len(runs),
        "shot_mean_sec": round(st.mean([d for _, d in runs]), 2) if runs else 0, "cameras": cams,
        "jump_cuts": jumps, "angle_change_at_jump": round(sw_jump / jumps, 2) if jumps else None,
        "continuous_cuts": cont, "angle_change_at_continuous": round(sw_cont / cont, 2) if cont else None,
        "removed_short_pauses": short_cut, "removed_big_chunks": sum(1 for g in gaps if g >= 10),
        "audio_enabled": audio_on, "camera_audio_disabled": audio_off_cams, "stacked_multicam": stacked,
    }

    # 화자 → 카메라: 켜진 마이크 파일을 이 PC 에서 찾을 수 있을 때만
    spk = speaker_camera_map(x, segs, R, media_dir, log)
    if spk:
        rules["speaker_cams"] = {k: v["camera"] for k, v in spk.items()}
        stats["speaker_cameras"] = spk
    return {"source": os.path.basename(path), "rules": rules, "stats": stats}


def speaker_camera_map(x: dict, segs: list[dict], R: float, media_dir: str | None, log=print) -> dict:
    import numpy as np
    from .media import MediaError, load_audio
    mics = defaultdict(list)   # 화자 이름 → [(clip, 로컬 경로)]
    for c in x["audio"]:
        if not c["enabled"]:
            continue
        f = x["files"][c["fid"]]
        local = _local(f["pathurl"], f["name"], media_dir)
        if local:
            mics[track_name(c["file"])].append((c, local))
    names = [n for n in mics if n.lower() not in ("lr", "mix")]
    if len(names) < 2:
        log("  (마이크 원본을 찾지 못해 화자 → 카메라 학습 생략: --media 로 원본 폴더를 알려주세요)")
        return {}
    cache: dict[str, np.ndarray] = {}

    def level(path, t0, t1):
        if path not in cache:
            try:
                sig = load_audio(path, 2000)
            except MediaError:
                sig = np.zeros(1, np.float32)
            n = len(sig) // 40
            cache[path] = 20 * np.log10(np.sqrt((sig[: n * 40].reshape(n, 40) ** 2).mean(axis=1)) + 1e-6)
        env = cache[path]
        a, b = int(t0 * 50), max(int(t1 * 50), int(t0 * 50) + 1)
        seg = env[a:b]
        return float(seg.mean()) if seg.size else -120.0

    norm = {}
    pair = Counter()
    for sgm in segs:
        scores = {}
        for n in names:
            for c, local in mics[n]:
                if c["start"] <= sgm["s"] < c["end"]:
                    src0 = (c["in"] + sgm["s"] - c["start"]) / R
                    scores[n] = level(local, src0, src0 + (sgm["e"] - sgm["s"]) / R)
        if len(scores) < 2:
            continue
        for n in scores:
            if n not in norm:
                env = cache[mics[n][0][1]]
                norm[n] = float(np.percentile(env, 90))
        best = max(scores, key=lambda n: scores[n] - norm[n])
        pair[(best, sgm["cam"])] += (sgm["e"] - sgm["s"]) / R
    out = {}
    for n in names:
        row = {cam: sec for (s_, cam), sec in pair.items() if s_ == n}
        if row:
            cam = max(row, key=row.get)
            out[n] = {"camera": cam, "share": round(row[cam] / sum(row.values()), 2)}
    return out


def save(result: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return out_path


def summary(result: dict) -> str:
    s, r = result["stats"], result["rules"]
    ko = {"front": "정면(기본)", "side": "측면", "wide": "컷어웨이/와이드", "tele": "망원"}
    lines = [f"[{s['sequence']}] {s['duration_sec'] / 60:.1f}분, 컷 {s['cuts']}개, 샷 {s['shots']}개(평균 {s['shot_mean_sec']}초)",
             "카메라별:"]
    for cam, v in sorted(s["cameras"].items(), key=lambda kv: -kv[1]["share"]):
        lines.append(f"  {cam:<10} {v['share']:.0%}  샷 {v['shots']}개  평균 {v['mean']}초 / 중앙 {v['median']}초 / 최대 {v['max']}초"
                     f"  → {ko.get(r['camera_roles'].get(cam), '')}")
    if s["angle_change_at_continuous"] is not None:
        lines.append(f"이어지는 발화 중 컷 {s['continuous_cuts']}개 → 앵글 전환 {s['angle_change_at_continuous']:.0%}")
    if s["angle_change_at_jump"] is not None:
        lines.append(f"내용을 잘라낸 컷 {s['jump_cuts']}개 → 앵글 전환 {s['angle_change_at_jump']:.0%}"
                     f" (짧은 쉼 제거 {s['removed_short_pauses']}, 10초 이상 삭제 {s['removed_big_chunks']})")
    lines.append(f"켜 둔 오디오: {', '.join(s['audio_enabled'])} / 꺼 둔 카메라 오디오: {', '.join(s['camera_audio_disabled']) or '없음'}")
    lines.append(f"멀티캠 쌓기: {'예' if s['stacked_multicam'] else '아니오'}, 시퀀스 {r['sequence_fps']}fps {r['sequence_width']}x{r['sequence_height']}")
    if s.get("speaker_cameras"):
        lines.append("화자 → 카메라: " + ", ".join(f"{k}→{v['camera']}({v['share']:.0%})" for k, v in s["speaker_cameras"].items()))
    lines.append(f"→ 규칙: max_hold {r['max_hold']}초, alt_hold {r['alt_hold']}초, min_shot {r['min_shot']}초"
                 + (f", wide_hold {r['wide_hold']}초" if "wide_hold" in r else ""))
    return "\n".join(lines)
