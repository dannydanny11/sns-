"""사용법:

  python -m autocut run input/<프로젝트> --preset 연수 --script script/<대본>.txt
  python -m autocut run input/<프로젝트> --preset 세미지 --target 20m
  python -m autocut run input/<프로젝트> --preset 교과서여행 --target shorts
  python -m autocut run input/<프로젝트> --preset 연수 --until sync        # 싱크까지만 하고 확인
  python -m autocut run input/<프로젝트> --preset 연수 --redo sync        # 싱크 다시 계산
  python -m autocut run input/<프로젝트> --preset 연수 --overrides output/<프로젝트>/<프로젝트>_수정.json
"""
from __future__ import annotations

import argparse
import sys

from .pipeline import STAGES, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autocut", description="촬영본 폴더 → 싱크·전사·컷 선별·앵글 배치·프리미어 XML")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="파이프라인 실행")
    r.add_argument("project", help="input/<프로젝트> 폴더")
    r.add_argument("--preset", required=True, help="config/presets.json 의 프리셋 이름(연수/세미지/교과서여행)")
    r.add_argument("--script", help="최종 대본 텍스트 파일(있으면 대본 대조 모드)")
    r.add_argument("--target", help="목표 길이: 20m, 90s, 1200, shorts")
    r.add_argument("--until", choices=STAGES, default="export", help="이 단계까지만 실행")
    r.add_argument("--redo", default="", help="다시 계산할 단계(쉼표 구분): sync,transcribe (cut·export 는 항상 재계산)")
    r.add_argument("--overrides", help="타임라인 뷰어에서 저장한 수정 JSON(테이크 채택/탈락 변경)")
    r.add_argument("--proxy", action="store_true", help="타임라인 뷰어 미리보기용 저해상도 프록시 생성(MXF·HEVC·4K 원본일 때)")
    r.add_argument("--work", help="작업 폴더(기본 edit_pipeline/work)")
    r.add_argument("--output", help="출력 폴더(기본 edit_pipeline/output)")
    a = ap.parse_args(argv)
    redo = {x.strip() for x in a.redo.split(",") if x.strip()}
    run(a.project, a.preset, a.script, a.target, a.until, redo, a.work, a.output, a.overrides, a.proxy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
