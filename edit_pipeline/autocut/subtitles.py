"""대사 자막(SRT), 포인트 자막 계획, 유튜브 챕터 타임스탬프.

모두 컷 편집된 시퀀스 시간축 기준이다. 대본 모드에서는 대본 문장(맞춤법이 정확함)을,
자유 모드에서는 전사 단어를 자막으로 쓴다. 타이밍은 단어별 타임코드에서 가져온다.
"""
from __future__ import annotations

import os

from .script import norm
from .timeline import rec_time


def _ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str, max_chars: int) -> list[str]:
    """어절 단위로 max_chars 이하 조각으로 나눈다."""
    out, cur = [], ""
    for tok in text.split():
        if cur and len(cur) + 1 + len(tok) > max_chars:
            out.append(cur)
            cur = tok
        else:
            cur = f"{cur} {tok}".strip()
    if cur:
        out.append(cur)
    return out


def _speaker_runs(item: dict, words: list[dict]) -> list[tuple[str | None, list[dict], str | None]]:
    """항목을 화자가 바뀌는 곳에서 나눈다: [(화자, 단어들, 대본 문장 또는 None)]."""
    ws = [words[i] for i in item["word_ids"]]
    if not ws:
        return []
    if item.get("script"):   # 대본 문장은 한 사람이 읽는다 → 문장 전체를 대표 화자로
        return [(item.get("spk"), ws, item["script"])]
    runs: list[tuple[str | None, list[dict], str | None]] = []
    for w in ws:
        spk = w.get("spk") or item.get("spk")
        if runs and runs[-1][0] == spk:
            runs[-1][1].append(w)
        else:
            runs.append((spk, [w], None))
    return runs


def _timed_chunks(ws: list[dict], text: str, max_chars: int) -> list[tuple[str, float, float]]:
    """자막 조각과 원본(기준 트랙) 시각. 대본 문장은 발화 글자 비율로 단어 시각에 맞춘다."""
    chunks = _wrap(text, max_chars)
    total = sum(len(norm(c)) for c in chunks) or 1
    spoken = [len(norm(w["w"])) or 1 for w in ws]
    cum, acc = [], 0
    for n in spoken:
        acc += n
        cum.append(acc / sum(spoken))
    out, done = [], 0
    for c in chunks:
        a = done / total
        done += len(norm(c))
        b = done / total
        k0 = next((k for k, x in enumerate(cum) if x > a + 1e-9), len(ws) - 1)
        k1 = next((k for k, x in enumerate(cum) if x >= b - 1e-9), len(ws) - 1)
        out.append((c, ws[k0]["s"], ws[max(k0, k1)]["e"]))
    return out


def captions(parts: list[dict], mapping: list[dict], max_chars: int = 20, min_dur: float = 0.8) -> list[dict]:
    """러프컷 시간축 자막. 화자가 바뀌면 자막도 끊고, 자막마다 화자(spk)를 단다."""
    cues = []
    for part in parts:
        words = part["plan"]["words"]
        for it in part["plan"]["items"]:
            if not it["enabled"]:
                continue
            for spk, ws, script in _speaker_runs(it, words):
                text = script or " ".join(w["w"] for w in ws)
                for chunk, s, e in _timed_chunks(ws, text, max_chars):
                    rs, re_ = rec_time(mapping, s, part["label"]), rec_time(mapping, e, part["label"])
                    if rs is None or re_ is None:
                        continue
                    cues.append({"s": rs, "e": max(re_, rs + min_dur), "text": chunk, "spk": spk})
    for a, b in zip(cues, cues[1:]):  # 겹침 제거
        if a["e"] > b["s"]:
            a["e"] = max(a["s"] + 0.1, b["s"] - 0.001)
    return cues


def point_subtitles(parts: list[dict], mapping: list[dict], rules: dict) -> list[dict]:
    """강조 문구의 실제 발화 구간 + 앞뒤 여유, 최소·최대 노출 시간 적용."""
    pre, post = rules.get("point_pad_before", 0.2), rules.get("point_pad_after", 0.5)
    lo, hi = rules.get("point_min", 1.5), rules.get("point_max", 5.0)
    out = []
    for part in parts:
        for it in part["plan"]["items"]:
            if not it["enabled"]:
                continue
            for sp in it.get("emphasis", []):
                rs, re_ = rec_time(mapping, sp["s"], part["label"]), rec_time(mapping, sp["e"], part["label"])
                if rs is None or re_ is None:
                    continue
                s = max(0.0, rs - pre)
                e = min(max(re_ + post, s + lo), s + hi)
                out.append({"s": round(s, 3), "e": round(e, 3), "text": sp["text"]})
    return out


def write_srt(path: str, cues: list[dict], speaker_prefix: bool = False) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k, c in enumerate(cues, 1):
            text = f"[{c['spk']}] {c['text']}" if speaker_prefix and c.get("spk") else c["text"]
            f.write(f"{k}\n{_ts(c['s'])} --> {_ts(c['e'])}\n{text}\n\n")
    return path


def write_speaker_srts(folder: str, prefix: str, cues: list[dict]) -> dict[str, str]:
    """화자마다 SRT 하나(프리미어에서 화자별 캡션 트랙·색으로 쓰기 좋게). 화자 없는 자막은 '미확인'."""
    by: dict[str, list[dict]] = {}
    for c in cues:
        by.setdefault(c.get("spk") or "미확인", []).append(c)
    out = {}
    for spk, cs in by.items():
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in spk)
        out[spk] = write_srt(os.path.join(folder, f"{prefix}_{safe}.srt"), cs)
    return out


def _tc(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def transcript_text(cues: list[dict], markers: list[dict], rate, title: str) -> str:
    """셀렉츠 전사 패널처럼: 주제(마커) 제목 아래 [시각] 화자  문장. 같은 화자가 이어 말하면 한 문단."""
    L = [f"# {title} — 러프컷 대본", "", "시각은 러프컷 시퀀스 기준(시:분:초).", ""]
    marks = sorted(((m["frame"] / rate, m["name"]) for m in markers), key=lambda x: x[0])
    mi = 0
    para, para_spk, last_e = None, None, 0.0
    for c in cues:
        while mi < len(marks) and marks[mi][0] <= c["s"] + 0.05:
            if para:
                L.append(para)
                para = None
            L += ["", f"## {marks[mi][1]}", ""]
            mi += 1
        spk = c.get("spk") or "-"
        if para and para_spk == spk and c["s"] - last_e < 2.0:
            para += " " + c["text"]
        else:
            if para:
                L.append(para)
            para, para_spk = f"[{_tc(c['s'])}] {spk}  {c['text']}", spk
        last_e = c["e"]
    if para:
        L.append(para)
    return "\n".join(L) + "\n"


def full_transcript(parts: list[dict]) -> str:
    """촬영 전체 전사(원본 시각, 세션별). 러프컷에서 빠진 부분은 ✂ 와 이유."""
    L = ["# 전체 전사(원본 기준)", "", "✂ = 러프컷에서 뺀 부분(이유). 시각은 각 녹음 세션의 시작부터.", ""]
    for p in parts:
        L += [f"## {p['label'].split('__', 1)[-1]}", ""]
        words = p["plan"]["words"]
        for it in p["plan"]["items"]:
            for spk, ws, script in _speaker_runs(it, words):
                text = script or " ".join(w["w"] for w in ws)
                mark = "" if it["enabled"] else f"✂({it['reason'] or '제외'}) "
                L.append(f"[{_tc(ws[0]['s'])}] {spk or '-'}  {mark}{text}")
        L.append("")
    return "\n".join(L)


def youtube_chapters(markers: list[dict], rate) -> str:
    lines = []
    for m in markers:
        t = int(m["frame"] / rate)
        h, rem = divmod(t, 3600)
        mm, ss = divmod(rem, 60)
        stamp = f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"
        lines.append(f"{stamp} {m['name']}")
    if lines and not lines[0].startswith(("0:00", "0:00:00")):
        lines[0] = "0:00 " + lines[0].split(" ", 1)[1]  # 유튜브는 0:00 으로 시작해야 챕터로 인식
    return "\n".join(lines) + ("\n" if lines else "")
