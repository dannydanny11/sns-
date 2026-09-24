"""촬영본 통째 폴더 자동 정리.

카드 폴더(PRIVATE/M4ROOT/CLIP, DCIM/100CANON …), 녹음기 파일, 인서트 영상이 섞여 있어도
1) 기준 녹음을 고르고(같은 순간을 녹음한 트랙은 하나만)
2) 모든 영상 파일을 하나씩 소리로 싱크하고
3) 폴더·기종·파일명 패턴·해상도로 묶은 뒤 시간이 겹치는 파일은 다른 카메라로 떼어 내고
4) 폴더 이름 → 얼굴 분석 → 촬영 분량 순으로 카메라 역할(정면/측면/망원/와이드/2인)을 정한다.
결과는 sync.sync_place 와 같은 형식이다.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import asdict

from . import sync
from .media import AUDIO_EXT, VIDEO_EXT

SKIP_DIRS = {"sub", "proxy", "proxies", "thmbnl", "thumbnail", "thumbnails", "general", "avf_info",
             "cache", "__macosx", ".spotlight-v100", ".trashes", ".fseventsd"}
SKIP_EXT = {".lrv", ".thm", ".xml", ".bim", ".cpi", ".mpl", ".bdm", ".ppn", ".smi", ".jpg", ".jpeg", ".png",
            ".dng", ".arw", ".cr2", ".cr3", ".heic", ".txt", ".pdf", ".json", ".srt"}
CARD_DIRS = {"private", "m4root", "clip", "dcim", "avchd", "bdmv", "stream", "xdroot", "contents", "video",
             "mp_root", "100media", "101media"}
ROLE_WORDS = {
    "front": ["정면", "front", "메인", "main", "a캠", "acam", "cama", "center", "센터"],
    "side": ["측면", "side", "b캠", "bcam", "사이드", "camb"],
    "tele": ["망원", "tele", "클로즈", "close", "closeup", "cu", "바스트", "tight"],
    "wide": ["와이드", "wide", "풀샷", "full", "fullshot", "ws", "전경"],
    "two": ["2인", "투샷", "two", "2shot", "twoshot"],
}


def collect(project_dir: str) -> tuple[list[str], list[str], list[str]]:
    """(영상, 오디오, 건너뛴 파일)"""
    videos, audios, skipped = [], [], []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS)
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = os.path.abspath(os.path.join(root, name))
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXT:
                videos.append(path)
            elif ext in AUDIO_EXT:
                audios.append(path)
            elif ext not in SKIP_EXT:
                skipped.append(path)
    return videos, audios, skipped


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z가-힣]+", text.lower()) if t]


def folder_of(path: str, project_dir: str) -> str:
    """카드 구조(DCIM/100CANON 등)를 걷어낸, 사람이 붙인 가장 가까운 폴더 경로."""
    rel = os.path.relpath(os.path.dirname(path), project_dir)
    parts = [] if rel == "." else rel.replace("\\", "/").split("/")
    human = [p for p in parts if p.lower() not in CARD_DIRS and not re.fullmatch(r"\d{3}[a-z_]{0,8}", p.lower())]
    return "/".join(human)


def role_from_words(text: str) -> str | None:
    toks = _tokens(text)
    for role, words in ROLE_WORDS.items():
        for w in words:
            korean = bool(re.search(r"[가-힣]", w))
            if w in toks or (korean and any(w in t for t in toks)):   # 한글은 '정면카메라' 처럼 붙여 써도 인식
                return role
    return None


def _prefix(name: str) -> str:
    stem = os.path.splitext(name)[0]
    return re.sub(r"\d+", "#", re.sub(r"[_-]?(tr\d|lr|m01)$", "", stem.lower()))


def _name_tokens(path: str) -> list[str]:
    stem = os.path.splitext(os.path.basename(path))[0]
    return [t for t in re.split(r"[_\s-]+", stem) if t]


def signature(clip: dict, project_dir: str) -> tuple:
    m = clip.get("media", {})
    return (folder_of(clip["file"], project_dir), m.get("model") or "", m.get("serial") or "",
            _prefix(os.path.basename(clip["file"])), m.get("width"), m.get("height"),
            round(m.get("fps") or 0, 2), m.get("vcodec") or "")


def split_overlaps(clips: list[dict], tol: float = 0.5) -> list[list[dict]]:
    """같은 카메라는 동시에 두 파일을 찍을 수 없다. 겹치면 다른 카메라로 나눈다."""
    chains: list[list[dict]] = []
    for c in sorted(clips, key=lambda c: c["offset"]):
        end = lambda ch: ch[-1]["offset"] + ch[-1]["duration"] * ch[-1].get("rate", 1.0)  # noqa: E731
        free = [ch for ch in chains if end(ch) <= c["offset"] + tol]
        if free:
            max(free, key=end).append(c)   # 바로 앞에서 끝난 체인에 잇는다(분할 파일 연속성)
        else:
            chains.append([c])
    return chains


def _time(iso: str | None):
    from datetime import datetime
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def continue_split_files(synced: list[tuple[dict, dict | None]], project_dir: str) -> list[dict]:
    """synced 를 제자리에서 고친다. 이어 붙인 클립 목록을 돌려준다."""
    by_dir: dict[str, list[int]] = defaultdict(list)
    for i, (c, _) in enumerate(synced):
        by_dir[os.path.dirname(c["file"])].append(i)
    placed = []
    for idxs in by_dir.values():
        idxs.sort(key=lambda i: os.path.basename(synced[i][0]["file"]))
        for a, b in zip(idxs, idxs[1:]):
            prev, pref = synced[a]
            cur, cref = synced[b]
            if cref is not None or pref is None or not cur.get("media", {}).get("has_video"):
                continue
            if signature(prev, project_dir) != signature(cur, project_dir):
                continue
            t0, t1 = _time(prev["media"].get("created")), _time(cur["media"].get("created"))
            if t0 and t1:
                if abs((t1 - t0).total_seconds() - prev["duration"]) > 5:
                    continue   # 촬영 시각으로 보아 이어지는 파일이 아님(따로 찍은 인서트)
                how = "촬영 시각으로 확인"
            else:
                how = "촬영 시각 정보 없음 — 확인 필요"
            end = prev["offset"] + prev["duration"] * prev.get("rate", 1.0)
            if end > pref["duration"] + 1:
                continue   # 기준 녹음이 끝난 뒤라면 이 세션 파일이 아니다
            cur.update(offset=round(end, 4), rate=prev.get("rate", 1.0), status="ok",
                       reason=f"앞 파일에 이어 배치(분할 녹화, {how})")
            synced[b] = (cur, pref)
            placed.append(cur)
    return placed


def organize(project_dir: str, rules: dict, role_overrides: dict[str, str] | None = None, log=print) -> dict:
    videos, audios, skipped = collect(project_dir)
    warnings: list[str] = []
    log(f"  파일 발견: 영상 {len(videos)}개, 오디오 {len(audios)}개" + (f", 기타 {len(skipped)}개(무시)" if skipped else ""))

    # 1) 기준 녹음: 녹음기 파일 → 없으면 가장 긴 영상들
    refs, w = sync.choose_references(audios, rules, "recorder", log)
    warnings += w
    if not refs:
        refs, w = sync.choose_references(videos, rules, "camera", log, bundle=False)
        if refs:
            warnings.append("별도 녹음 파일이 없어 카메라 오디오를 기준으로 사용")
    if not refs:
        return {"place": os.path.basename(os.path.normpath(project_dir)), "sessions": [], "auto": True,
                "warnings": warnings + ["소리가 있는 파일이 없어 싱크 불가"]}
    coarse = sync.load_coarse(refs)

    # 2) 영상 파일 하나씩 싱크
    synced: list[tuple[dict, dict | None]] = []
    for k, path in enumerate(videos, 1):
        clip, ref = sync.sync_file(path, refs, coarse, rules)
        c = asdict(clip)
        synced.append((c, ref))
        state = f"기준 {os.path.basename(ref['file'])} +{c['offset']:.3f}s" if ref else f"싱크 불가({c['reason']})"
        if c.get("rate", 1.0) != 1.0:
            state += f", 시계 오차 {(c['rate'] - 1) * 1e6:+.0f}ppm 보정"
        log(f"  [{k}/{len(videos)}] {os.path.relpath(path, project_dir)} → {state}")

    # 2-0) 녹음이 여러 파일로 쪼개졌어도 같은 카메라가 양쪽에 걸쳐 있으면 한 시간축으로 잇는다
    links = sync.link_references(refs, [c for c, _ in synced], log)
    for i, (c, ref) in enumerate(synced):
        if ref:
            merged, shift = links[ref["file"]]
            if shift:
                c["offset"] = round(c["offset"] + shift, 4)
            synced[i] = (c, merged)
    refs = list({id(m): m for m, _ in links.values()}.values())

    # 2-1) 소리로 못 맞춘 파일 중 '분할 녹화의 다음 파일'은 앞 파일 바로 뒤에 붙인다
    #      (4GB·시간 제한으로 쪼개진 파일은 끊김 없이 이어지므로 말소리가 없어도 위치를 안다)
    placed = continue_split_files(synced, project_dir)
    for c in placed:
        log(f"  {os.path.relpath(c['file'], project_dir)} → {c['reason']}")
        if "확인 필요" in c["reason"]:
            warnings.append(f"{os.path.relpath(c['file'], project_dir)}: {c['reason']}")

    # 3) 카메라 묶기: 기준(세션)을 넘나드는 같은 카메라도 하나로 본다
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c, ref in synced:
        if ref:
            c["_ref"] = ref["file"]
            groups[signature(c, project_dir)].append(c)
    cameras: list[list[dict]] = []
    for sig in sorted(groups, key=lambda s: (s[0], s[3], s[1])):
        by_ref: dict[str, list[dict]] = defaultdict(list)
        for c in groups[sig]:
            by_ref[c["_ref"]].append(c)
        # 세션마다 겹침으로 나눈 체인을 k 번째끼리 합친다
        per_ref = [split_overlaps(v) for v in by_ref.values()]
        for i in range(max(len(ch) for ch in per_ref)):
            cameras.append([c for ch in per_ref if i < len(ch) for c in ch[i]])

    # 4) 이름·역할 — 카메라마다 폴더가 따로면 폴더 이름, 한 폴더에 섞여 있으면 파일 이름에서 다른 부분(R3, C400…)
    cams_info = []
    folders = [folder_of(clips[0]["file"], project_dir) for clips in cameras]
    stems = [set(_name_tokens(clips[0]["file"])) for clips in cameras]
    for n, clips in enumerate(cameras, 1):
        folder = folders[n - 1]
        media = clips[0].get("media", {})
        base = folder.split("/")[0] if folder else ""
        if folders.count(folder) > 1 or not base:
            others = [stems[k] for k in range(len(cameras)) if k != n - 1 and folders[k] == folder]
            common = set.intersection(stems[n - 1], *others) if others else set()
            distinct = [t for t in _name_tokens(clips[0]["file"]) if t not in common and not t.isdigit()]
            base = "_".join(distinct[:2]) if distinct and others else base
        name = re.sub(r"[^\w가-힣-]+", "_", base) or f"카메라{n}"
        if any(ci["name"] == name for ci in cams_info):
            name = f"{name}_{n}"
        role = role_from_words(folder + " " + os.path.basename(clips[0]["file"]))
        cams_info.append({"name": name, "clips": clips, "folder": folder, "model": media.get("model"),
                          "role": role, "evidence": "폴더 이름" if role else "",
                          "coverage": sum(c["duration"] for c in clips)})
    from . import visual
    visual.assign_roles(cams_info, rules, log)
    for ci in cams_info:
        if role_overrides and ci["name"] in role_overrides:
            ci["role"], ci["evidence"] = role_overrides[ci["name"]], "직접 지정(--roles)"
        for c in ci["clips"]:
            c["camera"], c["role"] = ci["name"], ci["role"]
        log(f"  {ci['name']}: {ci['role']} ({ci['evidence']}) — 파일 {len(ci['clips'])}개"
            + (f", {ci['model']}" if ci["model"] else ""))

    inserts = []
    for c, ref in synced:
        if not ref:
            c["camera"], c["role"] = "인서트", "insert"
            inserts.append(c)

    sessions = []
    for ref in refs:
        clips = [c for ci in cams_info for c in ci["clips"] if c.get("_ref") == ref["file"]]
        sessions.append({"reference": ref, "clips": sorted(clips, key=lambda c: (c["camera"], c["offset"]))})
    for ci in cams_info:
        for c in ci["clips"]:
            c.pop("_ref", None)
    sessions.sort(key=lambda s: (s["reference"]["media"].get("created") or "", s["reference"]["file"]))
    if inserts:
        warnings.append(f"녹음과 소리가 맞지 않는 영상 {len(inserts)}개는 인서트로 분리(러프컷에 넣지 않음)")
    return {
        "place": os.path.basename(os.path.normpath(project_dir)),
        "sessions": sessions,
        "auto": True,
        "cameras": [{k: v for k, v in ci.items() if k != "clips"} | {"files": len(ci["clips"])} for ci in cams_info],
        "inserts": inserts,
        "excluded_cameras": [],
        "skipped_files": [os.path.relpath(p, project_dir) for p in skipped],
        "warnings": warnings,
    }
