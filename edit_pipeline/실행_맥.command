#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "먼저 설치_맥.command 를 실행하세요."; read -p "엔터"; exit 1; fi
.venv/bin/python -m autocut menu
