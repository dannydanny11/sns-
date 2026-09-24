"""3단계: 컷 선별.

공통: 무음 제거(앞뒤 여유 유지), 필러·더듬은 단어 제거
대본 대조 모드: 전사 구절을 대본 문장에 정렬 → 같은 문장이 다시 나오면 재촬영으로 보고
               마지막(완결된) 테이크만 채택. 나머지는 삭제하지 않고 비활성으로 남긴다.
자유 모드(대본 없음): 바로 이어지는 같은 말 반복만 재촬영으로 판정.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .script import Sentence, norm

DEFAULT_FILLERS = ["어", "음", "으", "엄", "흠", "에", "어어", "음음", "아아", "으음"]
SOFT_FILLERS = ["그", "저", "아", "뭐", "이제", "그러니까", "막"]


# ─────────────────────────── 단어 정리 ───────────────────────────

def mark_words(words: list[dict], rules: dict) -> list[dict]:
    """각 단어에 drop 사유를 붙인다(없으면 None)."""
    fillers = set(rules.get("fillers", DEFAULT_FILLERS))
    soft = set(rules.get("soft_fillers", SOFT_FILLERS))
    iso_gap = rules.get("soft_filler_gap", 0.3)
    out = []
    for i, w in enumerate(words):
        n = norm(w["w"])
        prev_gap = w["s"] - words[i - 1]["e"] if i > 0 else 99
        next_gap = words[i + 1]["s"] - w["e"] if i + 1 < len(words) else 99
        drop = None
        if not n:
            drop = "기호"
        elif n in fillers:
            drop = "필러"
        elif n in soft and (prev_gap >= iso_gap and next_gap >= iso_gap):
            drop = "필러"
        elif i + 1 < len(words):
            nxt = norm(words[i + 1]["w"])
            stutter_word = w["w"].rstrip().endswith(("-", "…", "~"))
            if nxt and (stutter_word or (len(n) <= 2 and nxt.startswith(n) and nxt != n)
                        or (n == nxt and len(n) <= 2 and next_gap < 0.6)):
                drop = "더듬음"
        out.append({**w, "i": i, "n": n, "drop": drop})
    return out


def build_phrases(words: list[dict], gap: float) -> list[dict]:
    """쉼(gap 초 이상)이나 문장부호에서 끊어 구절을 만든다. drop 된 단어는 경계로만 쓴다."""
    phrases, cur = [], []
    last_e = None
    for w in words:
        if w["drop"]:
            continue
        if cur and (w["s"] - last_e > gap):
            phrases.append(cur)
            cur = []
        cur.append(w)
        last_e = w["e"]
        if w["w"].endswith((".", "?", "!")):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    return [_phrase(ws) for ws in phrases]


def _phrase(ws: list[dict]) -> dict:
    return {"words": ws, "s": ws[0]["s"], "e": ws[-1]["e"], "norm": "".join(w["n"] for w in ws)}


# ─────────────────────────── 대본 정렬 ───────────────────────────

def match(sent: str, phrase: str) -> dict:
    sm = SequenceMatcher(None, sent, phrase, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
    matched = sum(b.size for b in blocks)
    if not blocks or not phrase:
        return {"precision": 0.0, "lo": 0, "hi": 0, "pos": set()}
    pos = set()
    for b in blocks:
        pos.update(range(b.a, b.a + b.size))
    return {"precision": matched / len(phrase), "lo": blocks[0].a, "hi": blocks[-1].a + blocks[-1].size, "pos": pos}


def best_sentence(phrase_norm: str, sentences: list[Sentence], ptr: int, window: tuple[int, int] = (4, 8)):
    lo = max(0, ptr - window[0])
    hi = min(len(sentences), ptr + window[1] + 1)
    best = None
    for s in sentences[lo:hi]:
        m = match(s.norm, phrase_norm)
        key = (round(m["precision"], 3), -abs(s.idx - ptr - 0.5))
        if best is None or key > best[0]:
            best = (key, s.idx, m)
    return best


def assign(phrase: dict, sentences: list[Sentence], ptr: int, min_precision: float, depth: int = 0) -> list[tuple]:
    """구절을 (구절, 문장 idx 또는 None, match) 목록으로. 문장 경계를 넘는 구절은 나눈다."""
    found = best_sentence(phrase["norm"], sentences, ptr)
    if found and found[2]["precision"] < min_precision:
        glob = best_sentence(phrase["norm"], sentences, ptr, window=(len(sentences), len(sentences)))
        if glob and glob[2]["precision"] > found[2]["precision"]:
            found = glob
    prec = found[2]["precision"] if found else 0.0
    ws = phrase["words"]
    if prec < 0.9 and len(ws) >= 2 and depth < 3:
        best_split = None
        total = len(phrase["norm"]) or 1
        for k in range(1, len(ws)):
            left, right = _phrase(ws[:k]), _phrase(ws[k:])
            fl = best_sentence(left["norm"], sentences, ptr)
            if not fl or fl[2]["precision"] < min_precision:
                continue
            fr = best_sentence(right["norm"], sentences, fl[1])
            if not fr or fr[2]["precision"] < min_precision or fr[1] == fl[1]:
                continue
            score = (fl[2]["precision"] * len(left["norm"]) + fr[2]["precision"] * len(right["norm"])) / total
            if best_split is None or score > best_split[0]:
                best_split = (score, left, right, fl[1])
        if best_split and best_split[0] > prec + 0.1:
            _, left, right, lidx = best_split
            return assign(left, sentences, ptr, min_precision, depth + 1) + \
                assign(right, sentences, lidx, min_precision, depth + 1)
    if not found or prec < min_precision or len(phrase["norm"]) < 2:
        return [(phrase, None, None)]
    return [(phrase, found[1], found[2])]


def script_items(words: list[dict], sentences: list[Sentence], rules: dict) -> tuple[list[dict], list[int]]:
    min_precision = rules.get("min_precision", 0.6)
    min_coverage = rules.get("min_coverage", 0.7)
    restart_tol = rules.get("restart_tolerance_chars", 2)
    phrases = build_phrases(words, rules.get("phrase_gap", 0.5))

    takes: list[dict] = []
    ptr = 0
    for ph in phrases:
        for part, sidx, m in assign(ph, sentences, ptr, min_precision):
            if sidx is None:
                takes.append({"sentence": None, "words": part["words"], "pos": set(), "hi": 0})
                continue
            cur = takes[-1] if takes else None
            if cur and cur["sentence"] == sidx and m["lo"] >= cur["hi"] - restart_tol:
                cur["words"] += part["words"]           # 이어서 말함(문장 중간 쉼)
                cur["pos"] |= m["pos"]
                cur["hi"] = max(cur["hi"], m["hi"])
            else:
                takes.append({"sentence": sidx, "words": list(part["words"]), "pos": set(m["pos"]), "hi": m["hi"]})
            ptr = sidx

    by_sentence: dict[int, list[int]] = {}
    for ti, t in enumerate(takes):
        if t["sentence"] is not None:
            t["coverage"] = len(t["pos"]) / max(1, len(sentences[t["sentence"]].norm))
            by_sentence.setdefault(t["sentence"], []).append(ti)
    chosen = set()
    for sidx, tis in by_sentence.items():
        complete = [ti for ti in tis if takes[ti]["coverage"] >= min_coverage]
        chosen.add(complete[-1] if complete else max(tis, key=lambda ti: takes[ti]["coverage"]))

    items = []
    for ti, t in enumerate(takes):
        sidx = t["sentence"]
        if sidx is None:
            enabled, reason = rules.get("keep_off_script", False), "대본 외 발화"
        elif ti in chosen:
            enabled, reason = True, ""
        else:
            later = [x for x in by_sentence[sidx] if x > ti]
            enabled = False
            reason = "재촬영 이전 테이크" if later else "미완결 테이크"
        n_takes = len(by_sentence.get(sidx, [])) if sidx is not None else 0
        item = _item(t["words"], enabled, reason)
        if sidx is not None:
            s = sentences[sidx]
            item.update(sentence=sidx, script=s.text, para=s.para, chapter=s.chapter,
                        coverage=round(t["coverage"], 2), takes=n_takes)
        items.append(item)
    missing = [s.idx for s in sentences if s.idx not in by_sentence]
    return items, missing


# ─────────────────────────── 자유 모드 ───────────────────────────

def free_items(words: list[dict], rules: dict) -> list[dict]:
    phrases = build_phrases(words, rules.get("phrase_gap", 0.5))
    items = []
    for k, ph in enumerate(phrases):
        enabled, reason = True, ""
        if k + 1 < len(phrases):
            nxt = phrases[k + 1]
            a = ph["norm"]
            if len(a) >= 3 and nxt["s"] - ph["e"] < rules.get("repeat_window", 10.0):
                ratio = SequenceMatcher(None, a, nxt["norm"][: len(a)], autojunk=False).ratio()
                if ratio >= rules.get("repeat_ratio", 0.8):
                    enabled, reason = False, "반복(재촬영 추정)"
        items.append(_item(ph["words"], enabled, reason))
    return items


# ─────────────────────────── 공통 ───────────────────────────

def _item(ws: list[dict], enabled: bool, reason: str) -> dict:
    return {"s": ws[0]["s"], "e": ws[-1]["e"], "w0": ws[0]["i"], "w1": ws[-1]["i"],
            "word_ids": [w["i"] for w in ws], "text": " ".join(w["w"] for w in ws),
            "enabled": enabled, "reason": reason}


def segments_for(item: dict, words: list[dict], rules: dict) -> list[dict]:
    """항목 안의 살릴 단어를 이어 붙여 구간을 만든다. 긴 쉼·필러 자리는 잘라낸다."""
    join_gap = rules.get("join_gap", 0.35)
    ws = [words[i] for i in item["word_ids"]]
    segs: list[dict] = []
    for w in ws:
        if segs and w["s"] - segs[-1]["e"] <= join_gap:
            segs[-1]["e"] = w["e"]
        else:
            segs.append({"s": w["s"], "e": w["e"]})
    return segs


def pad_segments(items: list[dict], rules: dict, total: float) -> None:
    """앞뒤 여유를 주되 이웃 구간과 겹치지 않게 한다(원본 시간축 기준)."""
    before, after = rules.get("pad_before", 0.08), rules.get("pad_after", 0.15)
    all_segs = sorted((seg for it in items for seg in it["segments"]), key=lambda x: x["s"])
    for k, seg in enumerate(all_segs):
        lo = all_segs[k - 1]["e"] if k > 0 else 0.0
        hi = all_segs[k + 1]["s"] if k + 1 < len(all_segs) else total
        seg["s"] = round(max(seg["s"] - before, (lo + seg["s"]) / 2 if k > 0 else 0.0, 0.0), 3)
        seg["e"] = round(min(seg["e"] + after, (seg["e"] + hi) / 2 if k + 1 < len(all_segs) else total), 3)


def mark_topics(items: list[dict], rules: dict) -> None:
    """대본 모드는 문단이 바뀌는 곳, 자유 모드는 긴 쉼 뒤를 주제 전환으로 본다."""
    gap = rules.get("topic_gap", 2.5)
    scripted = any("para" in it for it in items)
    prev_para, prev_e, first = None, None, True
    for it in items:
        it["topic_start"] = False
        if not it["enabled"]:
            continue
        if scripted:
            if "para" in it:
                it["topic_start"] = first or it["para"] != prev_para
                prev_para, first = it["para"], False
        else:
            it["topic_start"] = prev_e is None or it["s"] - prev_e >= gap
            prev_e = it["e"]


def emphasis_spans(item: dict, words: list[dict], phrases: list[str]) -> list[dict]:
    """강조 문구가 실제로 발화된 구간(단어 타임코드)을 찾는다."""
    ws = [words[i] for i in item["word_ids"] if not words[i]["drop"]]
    if not ws:
        return []
    owner, text = [], ""
    for k, w in enumerate(ws):
        text += w["n"]
        owner += [k] * len(w["n"])
    spans = []
    for ph in phrases:
        target = norm(ph)
        if not target:
            continue
        sm = SequenceMatcher(None, text, target, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
        if not blocks or sum(b.size for b in blocks) / len(target) < 0.6:
            continue
        a0 = blocks[0].a
        a1 = blocks[-1].a + blocks[-1].size - 1
        spans.append({"text": ph, "s": ws[owner[a0]]["s"], "e": ws[owner[a1]]["e"]})
    return spans


def plan_cuts(transcript: dict, rules: dict, sentences: list[Sentence] | None = None) -> dict:
    words = mark_words(transcript["words"], rules)
    if sentences:
        items, missing = script_items(words, sentences, rules)
        mode = "script"
    else:
        items, missing = free_items(words, rules), []
        mode = "free"
    for it in items:
        it["segments"] = segments_for(it, words, rules)
        if sentences and it.get("sentence") is not None and sentences[it["sentence"]].emphasis:
            it["emphasis"] = emphasis_spans(it, words, sentences[it["sentence"]].emphasis)
    pad_segments(items, rules, transcript.get("duration") or (words[-1]["e"] + 1 if words else 0))
    mark_topics(items, rules)
    dropped = [{"i": w["i"], "w": w["w"], "s": w["s"], "reason": w["drop"]} for w in words if w["drop"]]
    return {"mode": mode, "items": items, "missing_sentences": missing, "dropped_words": dropped,
            "words": [{k: w[k] for k in ("w", "s", "e")} for w in words]}


def kept_duration(plan: dict) -> float:
    return sum(seg["e"] - seg["s"] for it in plan["items"] if it["enabled"] for seg in it["segments"])
