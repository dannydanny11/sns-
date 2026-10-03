"""처리 리포트(마크다운): 싱크 결과, 누락·실패, 탈락 테이크, 앵글 분포."""
from __future__ import annotations

import os
from collections import Counter


def _t(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build(project: str, preset: str, scan_warnings: list[str], syncs: list[dict], parts: list[dict],
          timeline: dict, rate, sentences, missing: list[int], outputs: dict[str, str]) -> str:
    L = [f"# 자동 컷편집 리포트 — {project}", "", f"- 프리셋: {preset}"]
    src = sum(p["session"]["reference"]["duration"] for p in parts)
    out = float(timeline["duration"] / rate) if timeline["duration"] else 0.0
    L.append(f"- 원본(기준 트랙) {_t(src)} → 러프컷 {_t(out)}" + (f" ({out / src:.0%})" if src else ""))
    for p in parts:
        tg = p["plan"].get("target")
        if tg:
            L.append(f"- 목표 길이 {_t(tg['seconds'])} → {_t(tg['result'])} (중요도 판정: {tg['scoring']})")
    L.append("")
    L.append("## 출력 파일")
    for label, path in outputs.items():
        L.append(f"- {label}: `{os.path.basename(path)}`")
    L.append("")

    for sy in syncs:
        if sy.get("cameras"):
            L += ["## 자동 정리(통째로 넣은 촬영본)", "", "| 카메라 | 역할 | 판정 근거 | 기종 | 파일 수 | 폴더 |", "| --- | --- | --- | --- | --- | --- |"]
            ko = {"front": "정면", "side": "측면", "tele": "망원", "wide": "와이드", "two": "2인"}
            for ci in sy["cameras"]:
                L.append(f"| {ci['name']} | {ko.get(ci['role'], ci['role'])} | {ci.get('evidence', '')} | "
                         f"{ci.get('model') or '-'} | {ci.get('files', '')} | {ci.get('folder') or '-'} |")
            L.append("")
            L.append("역할이 틀렸으면 `--roles \"카메라이름=tele\"` 로 다시 실행 (front/side/tele/wide/two).")
            if sy.get("inserts"):
                L += ["", f"녹음과 소리가 맞지 않아 인서트로 분리한 영상 {len(sy['inserts'])}개(싱크 타임라인 맨 뒤에 배치):"]
                L += [f"- {os.path.basename(c['file'])} ({c['reason']})" for c in sy["inserts"]]
            if sy.get("skipped_files"):
                L.append(f"\n무시한 파일 {len(sy['skipped_files'])}개(영상·오디오 아님): " + ", ".join(sy["skipped_files"][:8])
                         + (" …" if len(sy["skipped_files"]) > 8 else ""))
            L.append("")
    L.append("## 녹음(기준 트랙과 마이크)")
    for sy in syncs:
        for sess in sy["sessions"]:
            tr = sess["reference"].get("tracks") or []
            names = ", ".join(f"{t['name']}({t['offset']:+.3f}s)" for t in tr) or os.path.basename(sess["reference"]["file"])
            L.append(f"- {os.path.basename(sess['reference']['file'])} ({_t(sess['reference']['duration'])}): {names}")
    L.append("")
    L.append("## 싱크")
    L.append("| 장소 | 카메라 | 파일 | 오프셋(초) | 신뢰도 | 슬레이트 | 상태 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for sy in syncs:
        for sess in sy["sessions"]:
            for c in sess["clips"]:
                slate = c.get("slate") or {}
                slate_txt = "-" if not slate.get("found") else ("일치" if slate.get("agree") else "불일치")
                off = f"{c['offset']:.3f}" if c["offset"] is not None else "-"
                state = "정상" if c["status"] == "ok" else f"제외: {c['reason']}"
                if c["status"] == "ok" and c.get("reason"):
                    state = c["reason"]
                if c.get("rate", 1.0) != 1.0:
                    state += f" · 시계 오차 {(c['rate'] - 1) * 1e6:+.0f}ppm 보정"
                L.append(f"| {sy['place']} | {c['camera']} | {os.path.basename(c['file'])} | {off} | "
                         f"{c['confidence']:.2f} | {slate_txt} | {state} |")
        if sy.get("excluded_cameras"):
            L.append(f"\n> [{sy['place']}] 싱크 실패로 제외된 카메라: {', '.join(sy['excluded_cameras'])}\n")
    warns = scan_warnings + [w for sy in syncs for w in sy.get("warnings", [])]
    if warns:
        L += ["", "## 경고", *[f"- {w}" for w in warns]]

    L += ["", "## 컷 선별"]
    reasons = Counter()
    rows = []
    for p in parts:
        for it in p["plan"]["items"]:
            if not it["enabled"]:
                reasons[it["reason"]] += 1
                rows.append(f"| {p['label']} | {_t(it['s'])} | {it['reason']} | {it['text'][:50]} |")
        dropped = Counter(d["reason"] for d in p["plan"].get("dropped_words", []))
        if dropped:
            L.append(f"- [{p['label']}] 제거한 단어: " + ", ".join(f"{k} {v}개" for k, v in dropped.items()))
    kept = sum(1 for p in parts for it in p["plan"]["items"] if it["enabled"])
    L.append(f"- 채택 {kept}개 / 비활성 {sum(reasons.values())}개 ("
             + (", ".join(f"{k} {v}" for k, v in reasons.items()) or "없음") + ")")
    if rows:
        L += ["", "비활성 테이크(검토용 XML 에 꺼진 클립으로 남아 있음)", "",
              "| 세션 | 원본 시각 | 사유 | 내용 |", "| --- | --- | --- | --- |", *rows]
    if sentences is not None:
        if missing:
            L += ["", "### 대본에 있으나 발화를 찾지 못한 문장"]
            L += [f"- ({i + 1}) {sentences[i].text}" for i in missing]
        else:
            L.append("- 대본 문장 전부 매칭됨")

    L += ["", "## 앵글 분포"]
    cams = Counter()
    for c in timeline["video"]:
        cams[c["camera"]] += float((c["end"] - c["start"]) / rate)
    L.append(f"- 샷 {len(timeline['video'])}개, 평균 {out / max(1, len(timeline['video'])):.1f}초")
    for cam, sec in cams.most_common():
        L.append(f"- {cam}: {_t(sec)} ({sec / out:.0%})" if out else f"- {cam}")
    return "\n".join(L) + "\n"
