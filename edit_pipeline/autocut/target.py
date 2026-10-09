"""목표 길이 지정 모드: "40분을 20분으로", "쇼츠로".

주제 단위(대본 문단 또는 긴 쉼)로 묶고 중요도를 매겨, 목표 길이 안에서 높은 순으로 고른 뒤
원래 순서로 되돌린다. 중요도는 ANTHROPIC_API_KEY 가 있으면 Claude 가 판단하고,
없으면 정보 밀도(초당 글자 수) 휴리스틱을 쓴다.
"""
from __future__ import annotations

import json
import os
import re

SHORTS_SECONDS = 59.0


def parse_target(value: str | None) -> float | None:
    """'20m', '1200', '90s', '쇼츠', 'shorts' → 초."""
    if not value:
        return None
    v = value.strip().lower()
    if v in {"shorts", "short", "쇼츠", "숏츠"}:
        return SHORTS_SECONDS
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|min|분)?\s*(?:(\d+)\s*(s|초))?", v)
    if m and m.group(2):
        return float(m.group(1)) * 60 + (float(m.group(3)) if m.group(3) else 0)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|초)?", v)
    if m:
        return float(m.group(1))
    raise ValueError(f"목표 길이 형식을 알 수 없음: {value}")


def chunks_of(items: list[dict]) -> list[list[int]]:
    chunks: list[list[int]] = []
    for k, it in enumerate(items):
        if not it["enabled"]:
            continue
        if it.get("topic_start") or not chunks:
            chunks.append([])
        chunks[-1].append(k)
    return chunks


def _dur(items, idx):
    return sum(seg["e"] - seg["s"] for k in idx for seg in items[k]["segments"])


def _text(items, idx):
    return " ".join(items[k].get("script") or items[k]["text"] for k in idx)


def heuristic_scores(items, chunks) -> list[float]:
    out = []
    for idx in chunks:
        d = _dur(items, idx) or 1
        density = len(re.sub(r"\s", "", _text(items, idx))) / d
        emph = sum(len(items[k].get("emphasis", [])) for k in idx)
        out.append(density + 2.0 * emph)
    return out


def llm_scores(items, chunks, target: float, model: str, log=print) -> list[float] | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        log("  anthropic 패키지 없음 → 휴리스틱 사용")
        return None
    listing = "\n".join(
        f"[{i}] ({_dur(items, idx):.0f}초) {_text(items, idx)[:600]}" for i, idx in enumerate(chunks))
    prompt = (
        "교사 대상 연수 영상을 줄이려 합니다. 아래는 영상의 주제 단위 구간입니다.\n"
        f"전체를 약 {target:.0f}초로 줄일 때 남길 가치가 높은 순서대로 각 구간에 0~10점을 매겨주세요.\n"
        "핵심 개념 설명·구체적 예시는 높게, 인사·반복·곁가지는 낮게 평가합니다.\n"
        + ("쇼츠용이므로 앞뒤 맥락 없이도 이해되는 구간을 높게 평가하세요.\n" if target <= SHORTS_SECONDS else "")
        + "\n" + listing
    )
    schema = {
        "type": "object",
        "properties": {"scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "score": {"type": "number"}},
            "required": ["index", "score"], "additionalProperties": False}}},
        "required": ["scores"], "additionalProperties": False,
    }
    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as exc:
        log(f"  Claude 호출 실패({exc.__class__.__name__}) → 휴리스틱 사용")
        return None
    if resp.stop_reason != "end_turn":
        log(f"  Claude 응답 중단({resp.stop_reason}) → 휴리스틱 사용")
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    scores = [0.0] * len(chunks)
    for row in json.loads(text)["scores"]:
        if 0 <= row["index"] < len(chunks):
            scores[row["index"]] = float(row["score"])
    return scores


def apply_target(plan: dict, target: float, model: str = "claude-sonnet-5", log=print) -> dict:
    items = plan["items"]
    chunks = chunks_of(items)
    if not chunks:
        return plan
    scores = llm_scores(items, chunks, target, model, log)
    source = "claude" if scores else "heuristic"
    if not scores:
        scores = heuristic_scores(items, chunks)
    order = sorted(range(len(chunks)), key=lambda c: -scores[c])
    chosen, total = set(), 0.0
    for c in order:
        d = _dur(items, chunks[c])
        if total + d <= target or not chosen:
            chosen.add(c)
            total += d
    for c, idx in enumerate(chunks):
        if c not in chosen:
            for k in idx:
                items[k]["enabled"] = False
                items[k]["reason"] = "목표 길이 초과로 제외"
    # 첫 구간이 빠졌다면 다음 살아남은 항목이 주제 시작이 된다.
    for c in sorted(chosen):
        items[chunks[c][0]]["topic_start"] = True
    plan["target"] = {"seconds": target, "result": round(total, 1), "scoring": source,
                      "chunks": [{"items": idx, "score": round(scores[c], 2), "kept": c in chosen}
                                 for c, idx in enumerate(chunks)]}
    return plan
