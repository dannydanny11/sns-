"""편집 스타일 학습 + 싱크 정답 비교.

셀렉츠·프리미어·파이널컷에서 실제로 편집한 파일을 읽는다.
- FCP7 XML(`xmeml`, 프리미어/셀렉츠 '내보내기 → Final Cut Pro XML')
- FCPXML(`fcpxml` 1.x, 파이널컷 프로 X / 셀렉츠 '파이널컷' 내보내기) — 멀티캠 포함

학습하는 것(설계 문서의 '셀렉츠 역설계'):
- 시퀀스 설정, 카메라별 사용 비율·샷 길이 → 역할(기본/측면/컷어웨이)과 max_hold 등
- 컷 지점 성격(이어지는 발화 중 앵글 전환 비율, 내용을 잘라낸 컷에서 앵글 전환 비율)
- 켜 둔 오디오(개인 마이크) / 꺼 둔 카메라 오디오, 멀티캠 쌓기 여부
- 마이크 원본이 이 PC 에 있으면 구간마다 가장 크게 들어온 마이크(화자) ↔ 켜진 카메라 → 화자 → 카메라

싱크 정답(compare_sync): 파이널컷 멀티캠(셀렉츠 'Synced Sequence')이나 프리미어 시퀀스에 놓인 파일 위치를
이 도구의 싱크 결과와 파일별로 비교해 오차(ms·프레임)를 보여 준다.
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


# ─────────────────────────── 공통 ───────────────────────────

def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[_\s-]+", os.path.splitext(os.path.basename(name.replace("\\", "/")))[0]) if t]


def camera_keys(names: list[str]) -> dict[str, str]:
    """파일 이름에서 카메라를 가르는 부분만: 20260804_PD은비_R3.MP4 → R3. 공통 부분이 없으면 파일 이름."""
    toks = {n: _tokens(n) for n in names}
    common = set.intersection(*[set(t) for t in toks.values()]) if len(toks) > 1 else set()
    full = {n: os.path.splitext(os.path.basename(n.replace("\\", "/")))[0] for n in names}
    out = {}
    for n, t in toks.items():
        d = [x for x in t if x not in common and not x.isdigit()]
        out[n] = "_".join(d[:2]) if d and common else full[n]
    dup = Counter(out.values())
    return {n: (full[n] if dup[k] > 1 else k) for n, k in out.items()}   # 겹치면(MVI_9447·MVI_0014) 전체 이름


def _local(url: str, name: str, media_dir: str | None) -> str | None:
    """XML 에 적힌 원본 경로(또는 media_dir 안의 같은 이름 파일)가 이 PC 에 있으면 그 경로."""
    cands = []
    if url:
        p = unquote(urlparse(url).path)
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


def _T(s: str | None) -> Fraction:
    """FCPXML 시간 '1001/30000s' → 초(분수)."""
    s = (s or "0s").rstrip("s")
    return Fraction(s) if s else Fraction(0)


# ─────────────────────────── FCP7 XML(xmeml) ───────────────────────────

def _rate(el) -> Fraction:
    tb = int(el.findtext("rate/timebase") or 30)
    ntsc = (el.findtext("rate/ntsc") or "FALSE").upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def parse(path: str) -> dict:
    root = ET.parse(path).getroot()
    seq = root.find(".//sequence")
    rate = _rate(seq)
    files: dict[str, dict] = {}
    for f in root.iter("file"):
        if f.find("name") is not None:
            files[f.get("id")] = {"name": f.findtext("name"), "pathurl": f.findtext("pathurl") or ""}
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


def edit_from_xmeml(path: str) -> dict:
    x = parse(path)
    R = x["rate"]
    vid = x["video"]
    keys = camera_keys(sorted({c["file"] for c in vid}))
    active = sorted((c for c in vid if c["enabled"]), key=lambda c: c["start"])
    aud_on = [c for c in x["audio"] if c["enabled"]]
    first = min((c["track"] for c in aud_on), default=None)
    ref = sorted((c for c in aud_on if c["track"] == first), key=lambda c: c["start"])

    def src_of(start):
        for c in ref:
            if c["start"] <= start < c["end"]:
                return c["file"], float((c["in"] + start - c["start"]) / R)
        return None, None

    segs = []
    for c in active:
        sid, src = src_of(c["start"])
        segs.append({"s": float(c["start"] / R), "e": float(c["end"] / R), "cam": keys[c["file"]],
                     "src_id": sid, "src": src, "_start": c["start"], "_end": c["end"]})

    mics = defaultdict(list)
    for c in aud_on:
        f = x["files"][c["fid"]]
        mics[track_name(c["file"])].append((c, f["pathurl"], f["name"]))

    def mic_range(name, seg):
        for c, url, fname in mics.get(name, []):
            if c["start"] <= seg["_start"] < c["end"]:
                return url, fname, float((c["in"] + seg["_start"] - c["start"]) / R), seg["e"] - seg["s"]
        return None

    cam_names = set(keys.values())
    return {"name": x["name"], "fps": float(R), "width": x["width"], "height": x["height"], "segs": segs,
            "stacked": len({c["track"] for c in vid}) > 1 and len(vid) > len(active),
            "audio_on": sorted(mics), "mic_range": mic_range,
            "camera_audio_off": sorted({keys[c["file"]] for c in x["audio"] if not c["enabled"] and c["file"] in keys}
                                       & cam_names)}


# ─────────────────────────── FCPXML(파이널컷 X) ───────────────────────────

def _fcpx(path: str):
    root = ET.parse(path).getroot()
    assets = {a.get("id"): a for a in root.iter("asset")}
    formats = {f.get("id"): f for f in root.iter("format")}
    return root, assets, formats


def _asset_url(a) -> str:
    rep = a.find("media-rep")
    return rep.get("src") if rep is not None else a.get("src", "")


def multicam_positions(path: str) -> dict:
    """멀티캠 각도별로 파일 0초가 멀티캠 시간축 몇 초에 놓였는지 → {파일 이름: {"pos", "angle", "url", "video"}}."""
    root, assets, _ = _fcpx(path)
    out = {}
    for mc in root.iter("multicam"):
        for ang in mc.findall("mc-angle"):
            for it in ang:
                if it.tag != "asset-clip":
                    continue
                a = assets.get(it.get("ref"))
                if a is None:
                    continue
                file_in = _T(it.get("start")) - _T(a.get("start"))       # 파일 안에서의 시작점
                pos0 = _T(it.get("offset")) - file_in                     # 파일 0초의 멀티캠 위치
                out[a.get("name")] = {"pos": float(pos0), "angle": ang.get("name"), "url": _asset_url(a),
                                      "video": a.get("hasVideo") == "1", "start": float(_T(it.get("offset"))),
                                      "dur": float(_T(it.get("duration"))), "file_in": float(file_in)}
    return out


def edit_from_fcpxml(path: str) -> dict:
    root, assets, formats = _fcpx(path)
    project = root.find(".//project")
    seq = project.find("sequence") if project is not None else root.find(".//sequence")
    fmt = formats.get(seq.get("format"))
    fps = float(1 / _T(fmt.get("frameDuration"))) if fmt is not None and fmt.get("frameDuration") else 30.0
    spine = seq.find("spine")
    mc = root.find(".//multicam")
    angles, is_video = {}, {}
    if mc is not None:
        for ang in mc.findall("mc-angle"):
            angles[ang.get("angleID")] = ang
            is_video[ang.get("angleID")] = any(
                assets.get(i.get("ref")) is not None and assets[i.get("ref")].get("hasVideo") == "1"
                for i in ang if i.tag == "asset-clip")
    cam_ids = [k for k, v in is_video.items() if v]
    keys = camera_keys([angles[k].get("name") for k in cam_ids])
    cam_key = {k: keys[angles[k].get("name")] for k in cam_ids}

    segs, audio_on, cam_audio_off = [], set(), set()
    for c in spine:
        off, dur = _T(c.get("offset")), _T(c.get("duration"))
        if c.tag == "mc-clip":
            vid = [s.get("angleID") for s in c.findall("mc-source") if s.get("srcEnable") in ("video", "all")]
            for s_ in c.findall("mc-source"):
                if s_.get("srcEnable") not in ("audio", "all"):
                    continue
                on = any(r.get("active") == "1" for r in s_.findall("audio-role-source")) or not s_.findall("audio-role-source")
                aid = s_.get("angleID")
                if on and not is_video.get(aid):
                    audio_on.add(track_name(angles[aid].get("name")))
                elif not on and is_video.get(aid):
                    cam_audio_off.add(cam_key[aid])
            if vid:
                segs.append({"s": float(off), "e": float(off + dur), "cam": cam_key.get(vid[0], vid[0]),
                             "src_id": "multicam", "src": float(_T(c.get("start")))})
        elif c.tag == "asset-clip":
            a = assets.get(c.get("ref"))
            if a is not None and a.get("hasVideo") == "1":
                segs.append({"s": float(off), "e": float(off + dur), "cam": a.get("name"),
                             "src_id": a.get("name"), "src": float(_T(c.get("start")))})

    positions = multicam_positions(path)
    mic_clips = defaultdict(list)
    for fname, p in positions.items():
        if not p["video"]:
            mic_clips[track_name(p["angle"])].append((fname, p))

    def mic_range(name, seg):
        t = seg["src"]
        for fname, p in mic_clips.get(name, []):
            if p["start"] <= t < p["start"] + p["dur"]:
                return p["url"], fname, t - p["pos"], seg["e"] - seg["s"]
        return None

    return {"name": project.get("name") if project is not None else "", "fps": fps,
            "width": int(fmt.get("width")) if fmt is not None and fmt.get("width") else None,
            "height": int(fmt.get("height")) if fmt is not None and fmt.get("height") else None,
            "segs": segs, "stacked": mc is not None, "audio_on": sorted(audio_on), "mic_range": mic_range,
            "camera_audio_off": sorted(cam_audio_off),
            "markers": [{"t": float(_T(m.get("start"))), "name": m.get("value")} for m in root.iter("marker")],
            "captions": sum(1 for _ in root.iter("caption"))}


def load_edit(path: str) -> dict:
    with open(path, "rb") as f:
        head = f.read(4096).decode("utf-8", "replace")
    return edit_from_fcpxml(path) if "<fcpxml" in head else edit_from_xmeml(path)


# ─────────────────────────── 스타일 분석 ───────────────────────────

def analyze(path: str, media_dir: str | None = None, log=print) -> dict:
    ed = load_edit(path)
    segs = ed["segs"]
    jumps = cont = sw_jump = sw_cont = 0
    gaps = []
    for a, b in zip(segs, segs[1:]):
        if a["src"] is None or b["src"] is None:
            continue
        same = a["src_id"] == b["src_id"]
        gap = b["src"] - (a["src"] + a["e"] - a["s"]) if same else None
        switched = a["cam"] != b["cam"]
        if gap is not None and abs(gap) < 1.5 / ed["fps"]:
            cont += 1
            sw_cont += switched
        else:
            jumps += 1
            sw_jump += switched
            if gap is not None:
                gaps.append(gap)

    runs: list[tuple[str, float]] = []
    for sgm in segs:
        d = sgm["e"] - sgm["s"]
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

    main = cams[order[0]] if order else {"p90": 15, "median": 7}
    alts = [cams[k] for k in order[1:] if roles.get(k) == "side"] or [main]
    wide = [cams[k] for k in order if roles.get(k) == "wide"]
    rules = {
        "max_hold": round(main["p90"], 1),
        "alt_hold": round(st.median([a["median"] for a in alts]), 1),
        "min_shot": round(max(0.8, min(_q([d for _, d in runs], 0.1), 2.0)), 1),
        "multicam_stack": ed["stacked"],
        "camera_audio_tracks": bool(ed["camera_audio_off"]),
        "cover_jump_cuts": jumps == 0 or sw_jump / jumps >= 0.5,
        "switch_on_speaker": cont == 0 or sw_cont / cont >= 0.5,
        "camera_roles": roles,
        "sequence_fps": round(ed["fps"], 3), "sequence_width": ed["width"], "sequence_height": ed["height"],
    }
    if wide:
        rules["wide_hold"] = round(wide[0]["median"], 1)
    src = [s["src"] for s in segs if s["src"] is not None]
    stats = {
        "sequence": ed["name"], "duration_sec": round(sum(s["e"] - s["s"] for s in segs), 1), "cuts": len(segs),
        "shots": len(runs), "shot_mean_sec": round(st.mean([d for _, d in runs]), 2) if runs else 0, "cameras": cams,
        "jump_cuts": jumps, "angle_change_at_jump": round(sw_jump / jumps, 2) if jumps else None,
        "continuous_cuts": cont, "angle_change_at_continuous": round(sw_cont / cont, 2) if cont else None,
        "removed_short_pauses": sum(1 for g in gaps if 0 < g < 1.5), "removed_big_chunks": sum(1 for g in gaps if g >= 10),
        "audio_enabled": ed["audio_on"], "camera_audio_disabled": ed["camera_audio_off"], "stacked_multicam": ed["stacked"],
        "source_span_sec": round(max(src) - min(src), 1) if src and len({s["src_id"] for s in segs}) == 1 else None,
    }
    if ed.get("markers"):
        stats["markers"] = ed["markers"]
    spk = speaker_camera_map(ed, media_dir, log)
    if spk:
        rules["speaker_cams"] = {k: v["camera"] for k, v in spk.items()}
        stats["speaker_cameras"] = spk
    return {"source": os.path.basename(path), "rules": rules, "stats": stats}


def speaker_camera_map(ed: dict, media_dir: str | None, log=print) -> dict:
    import numpy as np
    from .media import MediaError, load_audio
    names = [n for n in ed["audio_on"] if n.lower() not in ("lr", "mix")]
    if len(names) < 2:
        return {}
    cache: dict[str, np.ndarray | None] = {}
    found = set()

    def env_of(url, fname):
        key = url or fname
        if key not in cache:
            local = _local(url, fname, media_dir)
            env = None
            if local:
                try:
                    sig = load_audio(local, 2000)
                    n = len(sig) // 40
                    env = 20 * np.log10(np.sqrt((sig[: n * 40].reshape(n, 40) ** 2).mean(axis=1)) + 1e-6)
                    found.add(fname)
                except MediaError:
                    pass
            cache[key] = env
        return cache[key]

    level90: dict[str, list] = defaultdict(list)
    rows = []
    for sgm in ed["segs"]:
        scores = {}
        for n in names:
            got = ed["mic_range"](n, sgm)
            if not got:
                continue
            url, fname, t0, dur = got
            env = env_of(url, fname)
            if env is None:
                continue
            a, b = int(t0 * 50), max(int((t0 + dur) * 50), int(t0 * 50) + 1)
            seg = env[max(0, a):b]
            if seg.size:
                scores[n] = float(seg.mean())
                level90[n].append(float(np.percentile(env, 90)))
        if len(scores) >= 2:
            rows.append((scores, sgm["cam"], sgm["e"] - sgm["s"]))
    if not rows:
        log("  (마이크 원본을 찾지 못해 화자 → 카메라 학습 생략: --media 로 원본 폴더를 알려주세요)")
        return {}
    norm = {n: float(np.median(v)) for n, v in level90.items()}
    pair = Counter()
    for scores, cam, d in rows:
        best = max(scores, key=lambda n: scores[n] - norm[n])
        pair[(best, cam)] += d
    out = {}
    for n in names:
        row = {cam: sec for (s_, cam), sec in pair.items() if s_ == n}
        if row:
            cam = max(row, key=row.get)
            out[n] = {"camera": cam, "share": round(row[cam] / sum(row.values()), 2)}
    return out


# ─────────────────────────── 싱크 정답 비교 ───────────────────────────

def truth_positions(path: str) -> dict[str, float]:
    """정답 파일의 파일별 위치(초). FCPXML 멀티캠은 절대 위치, FCP7 XML 은 같은 컷 안의 상대 위치(중앙값)."""
    with open(path, "rb") as f:
        head = f.read(4096).decode("utf-8", "replace")
    if "<fcpxml" in head:
        return {k: v["pos"] for k, v in multicam_positions(path).items()}
    x = parse(path)
    R = x["rate"]
    # 시간이 겹쳐 놓인 클립끼리의 상대 위치를 모두 이어서(그래프) 한 기준으로 맞춘다.
    # 컷 편집 시퀀스(겹친 클립 = 같은 컷)와 싱크 타임라인(겹친 클립 = 동시에 녹화) 모두에 맞다.
    clips = sorted(x["video"] + x["audio"], key=lambda c: c["start"])
    rel = defaultdict(list)
    for i, a in enumerate(clips):
        fa = os.path.basename(a["file"].replace("\\", "/"))
        pa = float((a["start"] - a["in"]) / R)
        for b in clips[i + 1:]:
            if b["start"] >= a["end"]:
                break
            fb = os.path.basename(b["file"].replace("\\", "/"))
            if fa != fb and len(rel[(fa, fb)]) < 50:
                d = float((b["start"] - b["in"]) / R) - pa
                rel[(fa, fb)].append(d)
                rel[(fb, fa)].append(-d)
    edges = defaultdict(dict)
    for (a, b), v in rel.items():
        edges[a][b] = st.median(v)
    out: dict[str, float] = {}
    for start in sorted(edges):
        if start in out:
            continue
        out[start] = 0.0 if not out else out.get(start, 0.0)
        queue = [start]
        while queue:
            a = queue.pop(0)
            for b, d in edges[a].items():
                if b not in out:
                    out[b] = out[a] + d
                    queue.append(b)
    return out


def compare_sync(truth_path: str, sync_json: str) -> dict:
    """이 도구의 싱크(work/sync/<프로젝트>.json)와 정답 위치를 세션별로 비교한다."""
    truth = truth_positions(truth_path)
    with open(sync_json, encoding="utf-8") as f:
        syncs = json.load(f)
    rows, missing = [], []
    for sy in syncs:
        for sess in sy["sessions"]:
            ours = {}
            for t in sess["reference"].get("tracks") or [{"file": sess["reference"]["file"], "offset": 0.0}]:
                ours[os.path.basename(t["file"])] = t["offset"]
            for c in sess["clips"]:
                if c["status"] == "ok" and c["offset"] is not None:
                    ours[os.path.basename(c["file"])] = c["offset"]
            common = [k for k in ours if k in truth]
            missing += [k for k in ours if k not in truth]
            if len(common) < 2:
                continue
            # 기준: 세션 기준 녹음(없으면 첫 파일). 나머지 파일의 (우리 - 정답) 상대 오차
            anchor = os.path.basename(sess["reference"]["file"])
            anchor = anchor if anchor in common else common[0]
            for k in common:
                if k == anchor:
                    continue
                err = (ours[k] - ours[anchor]) - (truth[k] - truth[anchor])
                rows.append({"session": os.path.basename(sess["reference"]["file"]), "file": k, "anchor": anchor,
                             "ours": round(ours[k] - ours[anchor], 4), "truth": round(truth[k] - truth[anchor], 4),
                             "error_ms": round(err * 1000, 1)})
    errs = [abs(r["error_ms"]) for r in rows]
    return {"rows": rows, "missing": sorted(set(missing)), "not_in_ours": sorted(set(truth) - {r["file"] for r in rows}
                                                                              - {r["anchor"] for r in rows}),
            "max_ms": max(errs) if errs else None, "median_ms": st.median(errs) if errs else None}


def compare_summary(res: dict, fps: float = 29.97) -> str:
    L = []
    for r in sorted(res["rows"], key=lambda r: -abs(r["error_ms"])):
        frames = r["error_ms"] / 1000 * fps
        flag = "  ← 확인" if abs(frames) >= 1 else ""
        L.append(f"  {r['file']:<44} 오차 {r['error_ms']:+8.1f}ms ({frames:+.2f}프레임){flag}")
    head = (f"비교한 파일 {len(res['rows'])}개 · 오차 중앙값 {res['median_ms']}ms · 최대 {res['max_ms']}ms"
            if res["rows"] else "겹치는 파일이 없어 비교 못 함(파일 이름이 같아야 함)")
    tail = []
    if res["not_in_ours"]:
        tail.append(f"정답에는 있는데 이 도구가 싱크하지 못한 파일 {len(res['not_in_ours'])}개: "
                    + ", ".join(res["not_in_ours"][:10]) + (" …" if len(res["not_in_ours"]) > 10 else ""))
    if res["missing"]:
        tail.append(f"이 도구만 싱크한 파일(정답에 없음) {len(res['missing'])}개: " + ", ".join(res["missing"][:10]))
    return "\n".join([head, *L, *tail])


# ─────────────────────────── 저장·요약 ───────────────────────────

def save(result: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return out_path


def summary(result: dict) -> str:
    s, r = result["stats"], result["rules"]
    ko = {"front": "정면(기본)", "side": "측면", "wide": "컷어웨이/와이드", "tele": "망원"}
    span = f", 원본 {s['source_span_sec'] / 60:.0f}분에서" if s.get("source_span_sec") else ""
    lines = [f"[{s['sequence']}] {s['duration_sec'] / 60:.1f}분{span}, 컷 {s['cuts']}개, 샷 {s['shots']}개(평균 {s['shot_mean_sec']}초)",
             "카메라별:"]
    for cam, v in sorted(s["cameras"].items(), key=lambda kv: -kv[1]["share"]):
        lines.append(f"  {cam:<16} {v['share']:.0%}  샷 {v['shots']}개  평균 {v['mean']}초 / 중앙 {v['median']}초 / 최대 {v['max']}초"
                     f"  → {ko.get(r['camera_roles'].get(cam), '')}")
    if s["angle_change_at_continuous"] is not None:
        lines.append(f"이어지는 발화 중 컷 {s['continuous_cuts']}개 → 앵글 전환 {s['angle_change_at_continuous']:.0%}")
    if s["angle_change_at_jump"] is not None:
        lines.append(f"내용을 잘라낸 컷 {s['jump_cuts']}개 → 앵글 전환 {s['angle_change_at_jump']:.0%}"
                     f" (짧은 쉼 제거 {s['removed_short_pauses']}, 10초 이상 삭제 {s['removed_big_chunks']})")
    lines.append(f"켜 둔 오디오: {', '.join(s['audio_enabled'])} / 꺼 둔 카메라 오디오: {', '.join(s['camera_audio_disabled']) or '없음'}")
    lines.append(f"멀티캠: {'예' if s['stacked_multicam'] else '아니오'}, 시퀀스 {r['sequence_fps']}fps {r['sequence_width']}x{r['sequence_height']}")
    if s.get("markers"):
        lines.append("마커: " + " / ".join(m["name"] for m in s["markers"] if m["name"]))
    if s.get("speaker_cameras"):
        lines.append("화자 → 카메라: " + ", ".join(f"{k}→{v['camera']}({v['share']:.0%})" for k, v in s["speaker_cameras"].items()))
    lines.append(f"→ 규칙: max_hold {r['max_hold']}초, alt_hold {r['alt_hold']}초, min_shot {r['min_shot']}초"
                 + (f", wide_hold {r['wide_hold']}초" if "wide_hold" in r else ""))
    return "\n".join(lines)
