"""번호로 고르는 실행 메뉴(실행.bat / 실행_맥.command 가 부른다).

명령어를 몰라도: 프로젝트 번호 → (대본·목표 길이·화자 이름·스타일은 엔터로 건너뛰기) → 실행 → 결과 폴더와 뷰어가 열린다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

from .pipeline import ROOT, run


def _pick(title: str, options: list[str], allow_none: bool = True) -> str | None:
    if not options:
        return None
    print(f"\n{title}")
    if allow_none:
        print("  0) 없음 / 건너뛰기")
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    while True:
        raw = input("번호: ").strip()
        if raw == "" and allow_none:
            return None
        if raw.isdigit():
            k = int(raw)
            if k == 0 and allow_none:
                return None
            if 1 <= k <= len(options):
                return options[k - 1]
        print("  목록에 있는 번호를 입력하세요.")


def _open(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            webbrowser.open("file://" + os.path.abspath(path))
    except OSError:
        pass


def main() -> int:
    inp = os.path.join(ROOT, "input")
    projects = sorted(d for d in os.listdir(inp) if os.path.isdir(os.path.join(inp, d)) and not d.startswith("."))
    print("=" * 56)
    print(" 영상 자동 컷편집")
    print("=" * 56)
    if not projects:
        print(f"\ninput 폴더가 비어 있습니다.\n  {inp}\n안에 프로젝트 폴더를 만들고 촬영본을 통째로 넣은 뒤 다시 실행하세요.")
        input("\n엔터를 누르면 닫힙니다.")
        return 1
    project = _pick("어떤 프로젝트를 편집할까요?", projects, allow_none=False)

    scripts = sorted(f for f in os.listdir(os.path.join(ROOT, "script")) if f.lower().endswith(".txt")) \
        if os.path.isdir(os.path.join(ROOT, "script")) else []
    script = _pick("대본 파일(연수처럼 대본을 읽은 촬영일 때):", scripts)
    target = input("\n목표 길이(예: 20m, 90s, shorts — 원본 흐름대로면 엔터): ").strip() or None
    speakers = input("화자 이름(예: TX01=이형,TX02=시온 — 없으면 엔터): ").strip() or None
    sdir = os.path.join(ROOT, "config", "styles")
    styles = sorted(os.path.splitext(f)[0] for f in os.listdir(sdir) if f.endswith(".json")) if os.path.isdir(sdir) else []
    style = _pick("편집 스타일(셀렉츠 작업에서 학습한 것):", styles)
    only_sync = input("싱크·카메라 판정까지만 먼저 확인할까요? (y/엔터=끝까지): ").strip().lower() == "y"

    print("\n작업을 시작합니다. 처음에는 전사(Whisper) 때문에 오래 걸릴 수 있어요.\n")
    res = run(os.path.join(inp, project), "auto", os.path.join(ROOT, "script", script) if script else None, target,
              "sync" if only_sync else "export", None, None, None, None, False, None, style, speakers)
    out_dir = os.path.join(ROOT, "output", project)
    if only_sync:
        print(f"\n싱크 결과: {os.path.join(ROOT, 'work', 'sync', project + '.json')}")
        print("이상 없으면 다시 실행해서 끝까지 진행하세요(싱크는 다시 하지 않습니다).")
    else:
        viewer = res.get("outputs", {}).get("타임라인 뷰어")
        print(f"\n완료! 결과 폴더: {out_dir}")
        print("프리미어 프로젝트(.prproj)는 프리미어의 [창 > 확장 > 영상 자동 컷편집] 패널에서 [프로젝트 만들기]"
              "\n(자동 만들기를 켜 두었다면 프리미어가 알아서 만듭니다).")
        _open(out_dir)
        if viewer:
            _open(viewer)
    input("\n엔터를 누르면 닫힙니다.")
    return 0
