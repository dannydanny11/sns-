"""5단계: FCP7 XML(xmeml v4) 내보내기.

프리미어 구버전·최신 버전, 다빈치, 파이널컷(변환) 모두 여는 교환 포맷.
컷 지점·타임코드·트랙 배치·오디오 트랙만 담는다. 볼륨·이펙트는 넣지 않는다.
"""
from __future__ import annotations

import os
import re
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import escape

from .media import timebase


def pathurl(path: str) -> str:
    p = path.replace("\\", "/")
    if not re.match(r"^[A-Za-z]:/", p):
        p = os.path.abspath(path).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p):      # 윈도우 드라이브 경로 → /C:/...
        p = "/" + p
    return "file://localhost" + quote(p, safe="/:")


def _rate(fps: float | None) -> str:
    base, ntsc = timebase(fps)
    return f"<rate><timebase>{base}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


class _Files:
    """같은 파일은 처음 한 번만 전체 정의, 이후엔 id 참조."""

    def __init__(self, fps: float, rate: Fraction):
        self.ids: dict[str, str] = {}
        self.fps, self.rate = fps, rate

    def ref(self, path: str, media: dict) -> str:
        if path in self.ids:
            return f'<file id="{self.ids[path]}"/>'
        fid = f"file-{len(self.ids) + 1}"
        self.ids[path] = fid
        # 파일 자체의 프레임레이트로 적는다(29.97 원본을 23.976 시퀀스에 올리는 셀렉츠 작업과 같은 방식)
        file_fps = media.get("fps") if media.get("has_video") and media.get("fps") else self.fps
        base, ntsc = timebase(file_fps)
        dur = int(round(media.get("duration", 0) * (Fraction(base * 1000, 1001) if ntsc else Fraction(base))))
        parts = [f'<file id="{fid}">', f"<name>{escape(os.path.basename(path))}</name>",
                 f"<pathurl>{escape(pathurl(path))}</pathurl>", _rate(file_fps), f"<duration>{dur}</duration>",
                 "<media>"]
        if media.get("has_video"):
            parts.append("<video><samplecharacteristics>"
                         f"{_rate(media.get('fps') or self.fps)}"
                         f"<width>{media.get('width') or 1920}</width><height>{media.get('height') or 1080}</height>"
                         "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>")
        if media.get("has_audio"):
            parts.append("<audio><samplecharacteristics><depth>16</depth>"
                         f"<samplerate>{media.get('sample_rate') or 48000}</samplerate></samplecharacteristics>"
                         f"<channelcount>{media.get('audio_channels') or 2}</channelcount></audio>")
        parts.append("</media></file>")
        return "".join(parts)


def _clip(cid: str, c: dict, files: _Files, fps: float, kind: str, track_index: int = 1) -> str:
    name = os.path.basename(c["file"])
    enabled = "TRUE" if c.get("enabled", True) else "FALSE"
    out = [f'<clipitem id="{cid}">', f"<name>{escape(name)}</name>", f"<enabled>{enabled}</enabled>",
           f"<duration>{int(round(c['media'].get('duration', 0) * files.rate))}</duration>", _rate(fps),
           f"<start>{c['start']}</start><end>{c['end']}</end><in>{c['in']}</in><out>{c['out']}</out>",
           files.ref(c["file"], c["media"])]
    if kind == "audio":
        out.append(f"<sourcetrack><mediatype>audio</mediatype><trackindex>{track_index}</trackindex></sourcetrack>")
    if c.get("why"):
        out.append(f"<comments><mastercomment1>{escape(c['why'])}</mastercomment1></comments>")
    out.append("</clipitem>")
    return "".join(out)


def sequence_xml(tl: dict, name: str, fps: float, width: int, height: int) -> str:
    base, ntsc = timebase(fps)
    rate = Fraction(base * 1000, 1001) if ntsc else Fraction(base)
    files = _Files(fps, rate)
    n = 0

    def cid(prefix):
        nonlocal n
        n += 1
        return f"clipitem-{prefix}{n}"

    v_tracks = []
    for clips in tl.get("video_tracks") or [tl["video"]]:
        v_tracks.append("<track>" + "".join(_clip(cid("v"), c, files, fps, "video") for c in clips) + "</track>")
    a_tracks = []
    tracks = tl.get("audio_tracks") or ([{"name": "녹음", "clips": tl["audio"]}] if tl["audio"] else [])
    for tr in tracks:
        # 트랙이 하나뿐이고 스테레오면 채널별로 A1/A2(모노 트랙 2개)에 배치한다.
        channels = max((c["media"].get("audio_channels") or 1 for c in tr["clips"]), default=1)
        chs = range(1, min(channels, 2) + 1) if len(tracks) == 1 else [1]
        for ch in chs:
            clips = "".join(_clip(cid("a"), c, files, fps, "audio", ch) for c in tr["clips"])
            a_tracks.append("<track>" + clips + "</track>")
    markers = "".join(
        f"<marker><name>{escape(m['name'])}</name><comment>{escape(m.get('comment', ''))}</comment>"
        f"<in>{m['frame']}</in><out>-1</out></marker>" for m in tl["markers"])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{tl["duration"]}</duration>{_rate(fps)}'
        f"<timecode>{_rate(fps)}<string>00:00:00:00</string><frame>0</frame>"
        f"<displayformat>{'DF' if ntsc and base == 30 else 'NDF'}</displayformat></timecode>"
        "<media><video><format><samplecharacteristics>"
        f"{_rate(fps)}<width>{width}</width><height>{height}</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>"
        f"{''.join(v_tracks)}</video>"
        f"<audio><numOutputChannels>2</numOutputChannels>{''.join(a_tracks)}</audio></media>"
        f"{markers}</sequence></xmeml>\n"
    )


def write(path: str, tl: dict, name: str, fps: float, width: int, height: int) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sequence_xml(tl, name, fps, width, height))
    return path
