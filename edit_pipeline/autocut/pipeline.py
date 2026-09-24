"""단계 실행기. 각 단계 결과는 work/ 아래 JSON 으로 남고, 다시 실행하면 재사용한다."""
from __future__ import annotations

import json
import os
import re

from . import angles, cut, fcpxml, report, subtitles, sync, target, timeline, transcribe
from .media import frame_rate
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


def run(project_dir: str, preset: str, script_path: str | None = None, target_len: str | None = None,
        until: str = "export", redo: set[str] | None = None, work_root: str | None = None,
        output_root: str | None = None, log=print) -> dict:
    redo = redo or set()
    rules = load_rules(preset)
    project = os.path.basename(os.path.normpath(project_dir))
    work = os.path.join(work_root or os.path.join(ROOT, "work"))
    out_dir = os.path.join(output_root or os.path.join(ROOT, "output"), project)
    stop_after = STAGES.index(until)

    # ── 1. 싱크 ─────────────────────────────
    places, scan_warnings = scan_project(project_dir)
    sync_path = os.path.join(work, "sync", f"{project}.json")
    if os.path.exists(sync_path) and "sync" not in redo:
        syncs = _load(sync_path)
        log(f"[1/5] 싱크: 기존 결과 사용 ({sync_path})")
    else:
        log("[1/5] 싱크")
        syncs = [sync.sync_place(p, rules, log) for p in places]
        _dump(sync_path, syncs)
    if stop_after == 0:
        return {"sync": syncs}

    sessions = [(sy["place"], s) for sy in syncs for s in sy["sessions"]]

    # ── 2. 전사 ─────────────────────────────
    sentences = None
    prompt = None
    if script_path:
        with open(script_path, encoding="utf-8") as f:
            sentences = parse_script(f.read())
        prompt = " ".join(s.text for s in sentences[:20])[:800]  # 용어 인식 힌트
    transcripts = {}
    for place, sess in sessions:
        label = _label(place, sess["reference"]["file"])
        tpath = os.path.join(work, "transcript", project, f"{label}.json")
        if os.path.exists(tpath) and "transcribe" not in redo:
            log(f"[2/5] 전사: 기존 결과 사용 ({label})")
        else:
            log(f"[2/5] 전사: {label}")
            _dump(tpath, transcribe.transcribe(sess["reference"]["file"], rules["whisper_model"],
                                               initial_prompt=prompt, log=log))
        transcripts[label] = _load(tpath)
    if stop_after == 1:
        return {"sync": syncs, "transcripts": transcripts}

    # ── 3. 컷 선별 ───────────────────────────
    parts = []
    for place, sess in sessions:
        label = _label(place, sess["reference"]["file"])
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
    fps = base_media.get("fps") or 29.97
    rate = frame_rate(fps)
    w, h = base_media.get("width") or 1920, base_media.get("height") or 1080
    tl = timeline.build_timeline(parts, rate)

    def pick_base(session, s, e):
        got = angles.CameraPool(session, rules["roles"]).pick(["base", "alt", "wide", "tele"], s, e)
        return got[1] if got else None

    review = timeline.build_review(parts, rate, pick_base)
    outputs = {
        "러프컷 XML": fcpxml.write(os.path.join(out_dir, f"{project}_러프컷.xml"), tl, f"{project} 러프컷", fps, w, h),
        "검토용 XML(탈락 테이크 포함)": fcpxml.write(os.path.join(out_dir, f"{project}_검토용.xml"), review,
                                          f"{project} 전체 테이크", fps, w, h),
    }
    caps = subtitles.captions(parts, tl["mapping"], rules["caption_max_chars"])
    outputs["대사 자막 SRT"] = subtitles.write_srt(os.path.join(out_dir, f"{project}_자막.srt"), caps)
    points = subtitles.point_subtitles(parts, tl["mapping"], rules)
    if points:
        outputs["포인트 자막 SRT"] = subtitles.write_srt(os.path.join(out_dir, f"{project}_포인트자막.srt"), points)
        _dump(os.path.join(work, "subtitle", f"{project}_point.json"), points)
    if tl["markers"]:
        chap = os.path.join(out_dir, f"{project}_챕터.txt")
        with open(chap, "w", encoding="utf-8") as f:
            f.write(subtitles.youtube_chapters(tl["markers"], rate))
        outputs["유튜브 챕터"] = chap
    rpath = os.path.join(out_dir, f"{project}_리포트.md")
    outputs["리포트"] = rpath
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(report.build(project, preset, scan_warnings, syncs, parts, tl, rate, sentences, missing, outputs))
    log(f"[5/5] 내보내기 완료 → {out_dir}")
    for k, v in outputs.items():
        log(f"  - {k}: {v}")
    return {"sync": syncs, "parts": parts, "timeline": tl, "outputs": outputs}
