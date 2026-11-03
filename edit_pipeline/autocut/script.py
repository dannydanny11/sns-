"""대본 파일 해석.

형식(UTF-8 텍스트):
  # 챕터 제목         → 주제 전환(챕터 마커, 와이드 샷)
  빈 줄               → 문단 구분(주제 전환)
  **강조 문구**        → 포인트 자막 후보 + 망원 샷
문장은 . ? ! 또는 줄바꿈으로 나눈다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_KEEP = re.compile(r"[0-9a-z가-힣ㄱ-ㆎ]")
_SENT_END = re.compile(r"(?<=[.?!。])\s+")
_EMPH = re.compile(r"\*\*(.+?)\*\*")


def norm(text: str) -> str:
    """비교용 정규화: 한글·영문·숫자만 남기고 소문자화."""
    return "".join(_KEEP.findall(text.lower()))


@dataclass
class Sentence:
    idx: int
    text: str             # 강조 표시를 뺀 원문
    norm: str
    para: int             # 문단 번호
    chapter: str | None   # 이 문장이 챕터 첫 문장이면 제목
    emphasis: list[str] = field(default_factory=list)


def parse_script(text: str) -> list[Sentence]:
    sentences: list[Sentence] = []
    para = 0
    pending_chapter: str | None = None
    new_para = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if not new_para:
                para += 1
            new_para = True
            continue
        if line.startswith("#"):
            if not new_para:
                para += 1
            pending_chapter = line.lstrip("#").strip() or None
            new_para = True
            continue
        for piece in _SENT_END.split(line):
            piece = piece.strip()
            if not piece:
                continue
            emphasis = [m.strip() for m in _EMPH.findall(piece)]
            clean = _EMPH.sub(r"\1", piece)
            n = norm(clean)
            if not n:
                continue
            sentences.append(Sentence(idx=len(sentences), text=clean, norm=n, para=para,
                                      chapter=pending_chapter, emphasis=emphasis))
            pending_chapter = None
        new_para = False
    return sentences
