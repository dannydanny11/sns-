#!/bin/bash
cd "$(dirname "$0")"
echo "== 영상 자동 컷편집 - 설치 (처음 한 번만) =="
if ! command -v python3 >/dev/null; then
  echo "파이썬이 없습니다. python.org 에서 Python 3.12 를 설치한 뒤 다시 실행하세요."
  open "https://www.python.org/downloads/"; read -p "엔터를 누르면 닫힙니다."; exit 1
fi
python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt \
  && echo "설치 완료! input 폴더에 촬영본을 넣고 실행_맥.command 를 더블클릭하세요." \
  || echo "설치 중 오류가 났습니다. 위 메시지를 알려 주세요."
read -p "엔터를 누르면 닫힙니다."
