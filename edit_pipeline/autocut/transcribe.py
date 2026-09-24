"""2단계: 전사 (faster-whisper, 한국어 고정, 단어 단위 타임스탬프).

전사 대상은 세션의 기준 오디오 하나뿐이다. 카메라별 전사는 하지 않는다.
결과 형식:
{"language": "ko", "duration": 812.3,
 "words":    [{"w": "안녕하세요", "s": 1.02, "e": 1.61, "p": 0.98}, ...],
 "segments": [{"text": "...", "s": 1.02, "e": 4.8}, ...]}
"""
from __future__ import annotations

import json
import os
import tempfile

from .media import extract_wav


def transcribe(audio_path: str, model_size: str = "large-v3", device: str = "auto",
               compute_type: str = "default", initial_prompt: str | None = None, log=print) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit("faster-whisper 가 필요합니다: pip install faster-whisper") from exc

    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_wav(audio_path, os.path.join(tmp, "ref.wav"))
        log(f"  Whisper({model_size}) 로드 중…")
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            wav,
            language="ko",                 # 언어 자동감지 끄기: 한국어 정확도에 필수
            word_timestamps=True,
            vad_filter=False,              # 무음 판정은 3단계에서 직접 한다
            condition_on_previous_text=False,  # 반복 테이크에서 환각 반복 방지
            initial_prompt=initial_prompt,
        )
        words, segs = [], []
        for seg in segments:
            segs.append({"text": seg.text.strip(), "s": round(seg.start, 3), "e": round(seg.end, 3)})
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append({"w": text, "s": round(w.start, 3), "e": round(w.end, 3),
                                  "p": round(w.probability, 3)})
            log(f"    {seg.start:7.1f}s  {seg.text.strip()[:60]}")
    return {"language": "ko", "duration": round(info.duration, 3), "words": words, "segments": segs}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
