"""사용법:

  촬영본을 input/<프로젝트>/ 에 폴더째(카드 폴더·녹음기 파일 그대로) 넣고:
  python -m autocut run input/<프로젝트>                                   # 유형·카메라·마이크 자동 판정
  python -m autocut run input/<프로젝트> --script script/<대본>.txt        # 연수(대본 대조)
  python -m autocut run input/<프로젝트> --target 20m | --target shorts
  python -m autocut run input/<프로젝트> --until sync                      # 싱크·카메라 판정까지만 확인
  python -m autocut run input/<프로젝트> --roles "카메라2=tele"            # 역할 판정이 틀렸을 때
  python -m autocut run input/<프로젝트> --redo sync                       # 싱크 다시 계산
  python -m autocut run input/<프로젝트> --overrides output/<프로젝트>/<프로젝트>_수정.json
  python -m autocut learn 셀렉츠편집.xml --media input/<프로젝트> --name 팟캐스트   # 편집 스타일 학습
  python -m autocut run input/<프로젝트> --style 팟캐스트                          # 학습한 스타일로 편집
  python -m autocut compare-sync 셀렉츠.fcpxml input/<프로젝트>                    # 셀렉츠 싱크와 비교
"""
from __future__ import annotations

import argparse
import os
import sys

from .pipeline import STAGES, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autocut", description="촬영본 폴더 → 싱크·전사·컷 선별·앵글 배치·프리미어 XML")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="파이프라인 실행")
    r.add_argument("project", help="input/<프로젝트> 폴더")
    r.add_argument("--preset", default="auto", help="연수/세미지/교과서여행. 생략하면 자동 판정")
    r.add_argument("--script", help="최종 대본 텍스트 파일(있으면 대본 대조 모드)")
    r.add_argument("--target", help="목표 길이: 20m, 90s, 1200, shorts")
    r.add_argument("--until", choices=STAGES, default="export", help="이 단계까지만 실행")
    r.add_argument("--redo", default="", help="다시 계산할 단계(쉼표 구분): sync,transcribe (cut·export 는 항상 재계산)")
    r.add_argument("--overrides", help="타임라인 뷰어에서 저장한 수정 JSON(테이크 채택/탈락 변경)")
    r.add_argument("--proxy", action="store_true", help="타임라인 뷰어 미리보기용 저해상도 프록시 생성(MXF·HEVC·4K 원본일 때)")
    r.add_argument("--roles", help="카메라 역할 직접 지정: '카메라2=tele,A캠=front' (front/side/tele/wide/two)")
    r.add_argument("--style", help="learn 으로 만든 편집 스타일(JSON 경로 또는 config/styles/ 의 이름)")
    r.add_argument("--work", help="작업 폴더(기본 edit_pipeline/work)")
    r.add_argument("--output", help="출력 폴더(기본 edit_pipeline/output)")
    le = sub.add_parser("learn", help="셀렉츠·프리미어에서 편집한 XML 로 편집 스타일 학습")
    le.add_argument("xml", help="편집 파일: FCP7 XML(프리미어) 또는 .fcpxml(파이널컷 X·셀렉츠)")
    le.add_argument("--media", help="원본 폴더(마이크 파일을 찾아 화자 → 카메라까지 학습)")
    le.add_argument("--name", help="저장 이름(기본: XML 파일 이름) → config/styles/<이름>.json")
    cs = sub.add_parser("compare-sync", help="셀렉츠·파이널컷 멀티캠(정답)과 이 도구의 싱크 결과를 파일별로 비교")
    cs.add_argument("truth", help="정답 파일: .fcpxml(멀티캠) 또는 FCP7 XML")
    cs.add_argument("project", help="input/<프로젝트> (먼저 run --until sync 로 싱크해 둘 것)")
    cs.add_argument("--work", help="작업 폴더(기본 edit_pipeline/work)")
    a = ap.parse_args(argv)
    if a.cmd == "compare-sync":
        from . import learn
        from .pipeline import ROOT
        proj = os.path.basename(os.path.normpath(a.project))
        path = os.path.join(a.work or os.path.join(ROOT, "work"), "sync", f"{proj}.json")
        if not os.path.exists(path):
            print(f"싱크 결과가 없습니다. 먼저: python -m autocut run {a.project} --until sync")
            return 1
        print(learn.compare_summary(learn.compare_sync(a.truth, path)))
        return 0
    if a.cmd == "learn":
        from . import learn
        from .pipeline import ROOT
        res = learn.analyze(a.xml, a.media)
        name = a.name or os.path.splitext(os.path.basename(a.xml))[0]
        path = learn.save(res, os.path.join(ROOT, "config", "styles", f"{name}.json"))
        print(learn.summary(res))
        print(f"\n저장: {path}\n쓰는 법: python -m autocut run input/<프로젝트> --style {name}")
        return 0
    redo = {x.strip() for x in a.redo.split(",") if x.strip()}
    run(a.project, a.preset, a.script, a.target, a.until, redo, a.work, a.output, a.overrides, a.proxy, a.roles,
        a.style)
    return 0


if __name__ == "__main__":
    sys.exit(main())
