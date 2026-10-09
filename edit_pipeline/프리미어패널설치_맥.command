#!/bin/bash
cd "$(dirname "$0")"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/kr.autocut.panel"
rm -rf "$DEST"; mkdir -p "$(dirname "$DEST")"; cp -R premiere_panel "$DEST"
echo "window.AUTOCUT_ROOT = \"$(pwd)\";" > "$DEST/config.js"
for v in 9 10 11 12 13; do defaults write com.adobe.CSXS.$v PlayerDebugMode 1; done
echo "설치 완료! 프리미어 프로를 다시 켜고 [창] - [확장] - [영상 자동 컷편집] 을 여세요."
read -p "엔터를 누르면 닫힙니다."
