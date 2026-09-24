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
