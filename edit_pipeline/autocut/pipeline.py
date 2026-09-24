"""단계 실행기. 각 단계 결과는 work/ 아래 JSON 으로 남고, 다시 실행하면 재사용한다."""
from __future__ import annotations

import json
import os
import re

from . import angles, audio, autosort, cut, fcpxml, report, subtitles, sync, target, timeline, transcribe, viewer
from .media import frame_rate, make_proxy
from .scan import scan_project
from .script import parse_script

STAGES = ["sync", "transcribe", "cut", "export"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rules(preset: str, config_path: str | None = None) -> dict:
    path = config_path or os.path.join(ROOT, "config", "presets.json")
    with open(path, encoding="utf-8") as f:
        presets = json.load(f)
    if preset not in presets or preset.startswith("_"):
        names = [k for k in presets if not k.startswith("_") and k != "default"]
        raise SystemExit(f"프리셋 '{preset}' 없음. 사용 가능: {', '.join(names)}")
    rules = dict(presets.get("default", {}))
    rules.update({k: v for k, v in presets[preset].items() if not k.startswith("_")})
    return rules


def _label(place: str, ref_file: str) -> str:
    return re.sub(r"[^\w가-힣.-]+", "_", f"{place}__{os.path.splitext(os.path.basename(ref_file))[0]}")


def _labels(sessions: list[tuple[str, dict]]) -> list[str]:
    out: list[str] = []
    for place, sess in sessions:
        base = lab = _label(place, sess["reference"]["file"])
        n = 2
        while lab in out:
            lab = f"{base}_{n}"
            n += 1
        out.append(lab)
    return out


def guess_preset(syncs: list[dict], script_path: str | None) -> tuple[str, str]:
    """촬영 유형 자동 판정: (프리셋, 이유)."""
    if script_path:
        return "연수", "대본이 있음"
    roles = {c["role"] for sy in syncs for s in sy["sessions"] for c in s["clips"] if c["status"] == "ok"}
    n_sess = sum(len(sy["sessions"]) for sy in syncs)
    if "two" in roles:
        return "세미지", "2인 구도 카메라가 있음"
    if len(syncs) > 1 or n_sess > 1:
        return "교과서여행", f"녹음 세션 {n_sess}개(여러 장소·테이크)"
    return "세미지", "단일 세션 대담·강의"


def parse_roles(text: str | None) -> dict[str, str]:
    """'카메라2=tele, A캠=front' → {'카메라2': 'tele', 'A캠': 'front'}"""
    out = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def apply_roles(syncs: list[dict], roles: dict[str, str], by_file: bool = False) -> None:
    """카메라 이름(by_file 이면 그 카메라 파일 이름 일부로도)이 맞으면 그 카메라 전체의 역할을 바꾼다."""
    cam_role = {}
    for sy in syncs:
        for s in sy["sessions"]:
            for c in s["clips"]:
                for key, role in roles.items():
                    base = os.path.splitext(os.path.basename(c["file"]))[0].lower()
                    if key == c["camera"] or (by_file and key.lower() in base):
                        cam_role[c["camera"]] = role
    for sy in syncs:
        for s in sy["sessions"]:
            for c in s["clips"]:
                if c["camera"] in cam_role:
                    c["role"] = cam_role[c["camera"]]
        for ci in sy.get("cameras", []):
            if ci["name"] in cam_role:
                ci["role"] = cam_role[ci["name"]]
                ci["evidence"] = "학습한 스타일" if by_file else "직접 지정(--roles)"


def load_style(path: str) -> dict:
    """learn 결과(JSON)의 rules. config/styles/<이름>.json 이름만 줘도 된다."""
    cand = path if os.path.exists(path) else os.path.join(ROOT, "config", "styles", f"{path}.json")
    with open(cand, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rules", data)


def _dump(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dedupe_across_sessions(parts: list[dict]) -> None:
    """녹음이 여러 파일로 나뉜 경우: 같은 문장이 여러 세션에서 채택되면 가장 나중 것만 남긴다."""
    last = {}
    for pi, p in enumerate(parts):
        for k, it in enumerate(p["plan"]["items"]):
            if it["enabled"] and it.get("sentence") is not None:
                last[it["sentence"]] = (pi, k)
    for pi, p in enumerate(parts):
        for k, it in enumerate(p["plan"]["items"]):
            sidx = it.get("sentence")
            if it["enabled"] and sidx is not None and last[sidx] != (pi, k):
                it["enabled"], it["reason"] = False, "재촬영 이전 테이크(다른 녹음 파일)"


def run(project_dir: str, preset: str | None = "auto", script_path: str | None = None, target_len: str | None = None,
        until: str = "export", redo: set[str] | None = None, work_root: str | None = None,
        output_root: str | None = None, overrides: str | None = None, proxy: bool = False,
        roles: str | None = None, style: str | None = None, speakers: str | None = None, log=print) -> dict:
    redo = redo or set()
    auto_preset = preset in (None, "", "auto")
    rules = load_rules("default" if auto_preset else preset)
    learned = load_style(style) if style else {}
    project = os.path.basename(os.path.normpath(project_dir))
    work = os.path.join(work_root or os.path.join(ROOT, "work"))
    out_dir = os.path.join(output_root or os.path.join(ROOT, "output"), project)
    stop_after = STAGES.index(until)

    # ── 1. 싱크 ─────────────────────────────
    places, scan_warnings = scan_project(project_dir)
    auto_sort = not any(p.cameras for p in places)
    if auto_sort:
        scan_warnings = []
    sync_path = os.path.join(work, "sync", f"{project}.json")
    if os.path.exists(sync_path) and "sync" not in redo:
        syncs = _load(sync_path)
        log(f"[1/5] 싱크: 기존 결과 사용 ({sync_path})")
    elif auto_sort:
        log("[1/5] 싱크: 폴더 구조 없이 통째로 넣은 촬영본 → 자동 정리")
        syncs = [autosort.organize(project_dir, rules, None, log)]
        _dump(sync_path, syncs)
    else:
        log("[1/5] 싱크")
        syncs = [sync.sync_place(p, rules, log) for p in places]
        _dump(sync_path, syncs)
    if learned.get("camera_roles"):
        apply_roles(syncs, learned["camera_roles"], by_file=True)
    apply_roles(syncs, parse_roles(roles))
    if auto_preset:
        preset, why = guess_preset(syncs, script_path)
        rules = load_rules(preset)
        log(f"  촬영 유형 자동 판정: {preset} ({why})")
    if learned:
        rules.update({k: v for k, v in learned.items() if k != "camera_roles"})
        log(f"  편집 스타일 적용: {os.path.basename(style)}")
    if stop_after == 0:
        return {"sync": syncs, "preset": preset}

    sessions = [(sy["place"], s) for sy in syncs for s in sy["sessions"]]
    labels = _labels(sessions)

    # ── 2. 전사 ─────────────────────────────
    sentences = None
    prompt = None
    if script_path:
        with open(script_path, encoding="utf-8") as f:
            sentences = parse_script(f.read())
        prompt = " ".join(s.text for s in sentences[:20])[:800]  # 용어 인식 힌트
    transcripts, peaks = {}, {}
    for (place, sess), label in zip(sessions, labels):
        tracks = sess["reference"].get("tracks") or []
        tpath = os.path.join(work, "transcript", project, f"{label}.json")
        if os.path.exists(tpath) and "transcribe" not in redo:
            log(f"[2/5] 전사: 기존 결과 사용 ({label})")
        else:
            src = sess["reference"]["file"]
            if len(tracks) > 1:   # 무선 마이크 여러 개: 모두 섞어서 전사(누구 말도 빠지지 않게)
                src = audio.mix_tracks(tracks, sess["reference"]["duration"],
                                       os.path.join(work, "mix", project, f"{label}.wav"))
                log(f"[2/5] 전사: {label} (마이크 {len(tracks)}개 믹스)")
            else:
                log(f"[2/5] 전사: {label}")
            _dump(tpath, transcribe.transcribe(src, rules["whisper_model"], initial_prompt=prompt, log=log))
        tr = _load(tpath)
        # 마이크별 음량: 화자 판정 + 뷰어 파형
        if tracks:
            named = audio.by_name(tracks, [audio.envelope(t, sess["reference"]["duration"]) for t in tracks])
            n = audio.assign_speakers(tr["words"], named)
            names = {**(rules.get("speaker_names") or {}), **parse_roles(speakers)}   # TX01=이형
            if names:
                for w in tr["words"]:
                    if w.get("spk") in names:
                        w["spk"] = names[w["spk"]]
            if n:
                log(f"  화자 판정: 마이크 {len(audio.speaker_names(list(named)))}개 기준, 단어 {n}개")
            peaks[label] = {name: audio.peaks(e) for name, e in named.items()}
        transcripts[label] = tr
    if stop_after == 1:
        return {"sync": syncs, "transcripts": transcripts}

    # ── 3. 컷 선별 ───────────────────────────
    parts = []
    for (place, sess), label in zip(sessions, labels):
        tr = transcripts[label]
        tr.setdefault("duration", sess["reference"]["duration"])
        plan = cut.plan_cuts(tr, rules, sentences)
        parts.append({"label": label, "place": place, "session": sess, "plan": plan})
        log(f"[3/5] 컷 선별: {label} — {plan['mode']} 모드, "
            f"채택 {sum(i['enabled'] for i in plan['items'])}/{len(plan['items'])}, "
            f"{cut.kept_duration(plan):.0f}초")
    if sentences:
        _dedupe_across_sessions(parts)
    missing = sorted(set.intersection(*[set(p["plan"]["missing_sentences"]) for p in parts])) if parts else []
    tsec = target.parse_target(target_len)
    if tsec:
        # 여러 세션이면 길이 비율대로 목표를 나눈다.
        total = sum(cut.kept_duration(p["plan"]) for p in parts) or 1
        for p in parts:
            share = tsec * cut.kept_duration(p["plan"]) / total
            target.apply_target(p["plan"], share, rules["llm_model"], log)
    if overrides:
        n = viewer.apply_overrides(parts, viewer.load_overrides(overrides))
        for p in parts:
            cut.mark_topics(p["plan"]["items"], rules)
        log(f"[3/5] 타임라인 뷰어에서 수정한 테이크 {n}개 반영")
    for p in parts:
        _dump(os.path.join(work, "plan", project, f"{p['label']}.json"), p["plan"])
    if stop_after == 2:
        return {"sync": syncs, "parts": parts}

    # ── 4. 앵글 배치 ─────────────────────────
    for p in parts:
        p["shots"] = angles.place_angles(p["plan"], p["session"], rules)
        log(f"[4/5] 앵글 배치: {p['label']} — 샷 {len(p['shots'])}개")

    # ── 5. 내보내기 ─────────────────────────
    base_media = next((c["media"] for p in parts for c in p["session"]["clips"]
                       if c["status"] == "ok" and c["role"] in rules["roles"]["base"]), None) or \
        next((c["media"] for p in parts for c in p["session"]["clips"] if c["status"] == "ok"), {})
    fps = rules.get("sequence_fps") or base_media.get("fps") or 29.97
    rate = frame_rate(fps)
    w = rules.get("sequence_width") or base_media.get("width") or 1920
    h = rules.get("sequence_height") or base_media.get("height") or 1080
    tl = timeline.build_timeline(parts, rate, rules.get("multicam_stack", True), rules.get("camera_audio_tracks", True))

    def pick_base(session, s, e):
        got = angles.CameraPool(session, rules["roles"]).pick(["base", "alt", "wide", "tele"], s, e)
        return got[1] if got else None

    review = timeline.build_review(parts, rate, pick_base)
    sync_tl = timeline.build_sync_timeline(syncs, rate)
    outputs = {
        "싱크 타임라인 XML(촬영 전체 멀티캠)": fcpxml.write(
            os.path.join(out_dir, f"{project}_싱크타임라인.xml"), sync_tl, f"{project} 싱크 타임라인", fps, w, h),
        "러프컷 XML": fcpxml.write(os.path.join(out_dir, f"{project}_러프컷.xml"), tl, f"{project} 러프컷", fps, w, h),
        "검토용 XML(탈락 테이크 포함)": fcpxml.write(os.path.join(out_dir, f"{project}_검토용.xml"), review,
                                          f"{project} 전체 테이크", fps, w, h),
    }
    caps = subtitles.captions(parts, tl["mapping"], rules["caption_max_chars"])
    outputs["대사 자막 SRT"] = subtitles.write_srt(os.path.join(out_dir, f"{project}_자막.srt"), caps)
    if any(c.get("spk") for c in caps):   # 셀렉츠처럼 화자별로
        outputs["화자 표시 자막 SRT"] = subtitles.write_srt(
            os.path.join(out_dir, f"{project}_자막_화자표시.srt"), caps, speaker_prefix=True)
        per = subtitles.write_speaker_srts(os.path.join(out_dir, f"{project}_자막_화자별"), project, caps)
        outputs["화자별 자막 SRT"] = os.path.join(out_dir, f"{project}_자막_화자별") + os.sep
        log("  화자별 자막: " + ", ".join(f"{k} {sum(1 for c in caps if (c.get('spk') or '미확인') == k)}줄" for k in per))
    with open(os.path.join(out_dir, f"{project}_대본.txt"), "w", encoding="utf-8") as f:
        f.write(subtitles.transcript_text(caps, tl["markers"], rate, project))
    outputs["러프컷 대본(화자·시각)"] = os.path.join(out_dir, f"{project}_대본.txt")
    with open(os.path.join(out_dir, f"{project}_전체전사.txt"), "w", encoding="utf-8") as f:
        f.write(subtitles.full_transcript(parts))
    outputs["전체 전사(원본·뺀 부분 표시)"] = os.path.join(out_dir, f"{project}_전체전사.txt")
    points = subtitles.point_subtitles(parts, tl["mapping"], rules)
    if points:
        outputs["포인트 자막 SRT"] = subtitles.write_srt(os.path.join(out_dir, f"{project}_포인트자막.srt"), points)
        _dump(os.path.join(work, "subtitle", f"{project}_point.json"), points)
    if tl["markers"]:
        chap = os.path.join(out_dir, f"{project}_챕터.txt")
        with open(chap, "w", encoding="utf-8") as f:
            f.write(subtitles.youtube_chapters(tl["markers"], rate))
        outputs["유튜브 챕터"] = chap
    proxies = {}
    if proxy:
        used = sorted({c["file"] for p in parts for c in p["session"]["clips"] if c["status"] == "ok"})
        ext = ".mp4" if rules.get("proxy_codec", "h264") == "h264" else ".webm"
        for k, path in enumerate(used, 1):
            log(f"  프록시 {k}/{len(used)}: {os.path.basename(path)}")
            name = re.sub(r"[^\w가-힣.-]+", "_", os.path.relpath(path, project_dir))
            proxies[path] = make_proxy(path, os.path.join(work, "proxy", project, os.path.splitext(name)[0] + ext),
                                       rules.get("proxy_height", 360), rules.get("proxy_codec", "h264"))
    outputs["타임라인 뷰어"] = viewer.write(
        os.path.join(out_dir, f"{project}_타임라인.html"),
        viewer.build_data(project, parts, tl, rate, caps, points, out_dir, proxies, peaks, syncs))
    # 프리미어 패널이 읽는 작업 목록: 이 파일이 있으면 패널이 .prproj 를 만든다(프리미어가 직접 변환)
    speaker_dir = os.path.join(out_dir, f"{project}_자막_화자별")
    job = {
        "project": project,
        "prproj": os.path.join(out_dir, f"{project}.prproj"),
        "rough_xml": outputs["러프컷 XML"],
        "extra_xml": [outputs["싱크 타임라인 XML(촬영 전체 멀티캠)"], outputs["검토용 XML(탈락 테이크 포함)"]],
        "captions": outputs["대사 자막 SRT"],
        "speaker_captions": {os.path.splitext(f)[0].split("_", 1)[-1]: os.path.join(speaker_dir, f)
                             for f in sorted(os.listdir(speaker_dir))} if os.path.isdir(speaker_dir) else {},
        "point_captions": outputs.get("포인트 자막 SRT"),
    }
    def rel(v):   # 폴더를 옮겨도 되도록 이 JSON 기준 상대 경로
        if isinstance(v, str) and os.path.isabs(v):
            return os.path.relpath(v, out_dir).replace("\\", "/")
        if isinstance(v, list):
            return [rel(x) for x in v]
        if isinstance(v, dict):
            return {k: rel(x) for k, x in v.items()}
        return v
    job_path = os.path.join(out_dir, f"{project}_프리미어.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({k: rel(v) for k, v in job.items()}, f, ensure_ascii=False, indent=1)
    outputs["프리미어 작업 목록(패널용)"] = job_path
    rpath = os.path.join(out_dir, f"{project}_리포트.md")
    outputs["리포트"] = rpath
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(report.build(project, preset, scan_warnings, syncs, parts, tl, rate, sentences, missing, outputs))
    log(f"[5/5] 내보내기 완료 → {out_dir}")
    for k, v in outputs.items():
        log(f"  - {k}: {v}")
    return {"sync": syncs, "parts": parts, "timeline": tl, "sync_timeline": sync_tl, "outputs": outputs,
            "preset": preset}
