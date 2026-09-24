"""여러 마이크 트랙 처리: 전사용 믹스, 화자 판정, 뷰어용 파형.

세션 기준 트랙의 "tracks" 에는 같은 순간을 녹음한 파일들이 기준 시간축 오프셋과 함께 들어 있다
(무선 마이크 TX01~TX04, 녹음기 Tr1/Tr2/LR 등). 트랙 시각 c 는 기준 시각 offset + rate×c 이다.
"""
from __future__ import annotations

import os
import subprocess

import numpy as np

from .media import MediaError, ffmpeg_exe, load_audio

ENV_HZ = 50           # 화자 판정용 음량 해상도(20ms)
PEAK_HZ = 5           # 뷰어 파형 해상도
MIX_NAMES = ("lr", "mix", "main", "master")


def mix_tracks(tracks: list[dict], duration: float, out_path: str, sr: int = 16000) -> str:
    """모든 트랙을 기준 시간축에 맞춰 섞은 모노 WAV(전사용)."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    chains = []
    for i, t in enumerate(tracks):
        cmd += ["-i", t["file"]]
        off = t["offset"]
        if off >= 0:
            chains.append(f"[{i}:a]aformat=channel_layouts=mono,adelay={int(off * 1000)}:all=1[a{i}]")
        else:
            chains.append(f"[{i}:a]aformat=channel_layouts=mono,atrim=start={-off:.3f},asetpts=PTS-STARTPTS[a{i}]")
    ins = "".join(f"[a{i}]" for i in range(len(tracks)))
    graph = ";".join(chains) + f";{ins}amix=inputs={len(tracks)}:normalize=0:duration=longest," \
                               f"atrim=0:{duration:.3f},alimiter=limit=0.9[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", str(sr), out_path]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"마이크 믹스 실패: {proc.stderr.decode(errors='replace')[-300:]}")
    return out_path


def envelope(track: dict, duration: float) -> np.ndarray:
    """트랙 음량(dB)을 기준 시간축 ENV_HZ 간격으로. 녹음 범위 밖은 NaN."""
    sig = load_audio(track["file"], 2000)
    hop = 2000 // ENV_HZ
    n = len(sig) // hop
    rms = np.sqrt((sig[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-6)
    frames = int(duration * ENV_HZ) + 1
    t_ref = np.arange(frames) / ENV_HZ
    j = np.round((t_ref - track["offset"]) / track.get("rate", 1.0) * ENV_HZ).astype(int)
    out = np.full(frames, np.nan, dtype=np.float32)
    ok = (j >= 0) & (j < n)
    out[ok] = db[j[ok]]
    return out


def speaker_tracks(tracks: list[dict]) -> list[int]:
    """화자 판정에 쓸 트랙(녹음기 믹스 LR 트랙은 개인 마이크가 따로 있으면 뺀다)."""
    idx = [i for i, t in enumerate(tracks) if t["name"].lower() not in MIX_NAMES]
    return idx if len(idx) >= 2 else []


def assign_speakers(words: list[dict], tracks: list[dict], envs: list[np.ndarray], margin_db: float = 3.0) -> int:
    """단어마다 가장 크게 들어온 개인 마이크를 화자로 붙인다. 붙인 단어 수를 돌려준다."""
    idx = speaker_tracks(tracks)
    if not idx:
        return 0
    # 마이크마다 게인이 달라서, 각 트랙의 '말할 때 음량'(상위 10%)을 기준으로 정규화한다.
    ref_level = [np.nanpercentile(envs[i], 90) if np.isfinite(envs[i]).any() else 0.0 for i in idx]
    n = 0
    prev = None
    for w in words:
        a, b = int(w["s"] * ENV_HZ), max(int(w["e"] * ENV_HZ), int(w["s"] * ENV_HZ) + 1)
        scores = []
        for k, i in enumerate(idx):
            seg = envs[i][a:b]
            seg = seg[np.isfinite(seg)]
            scores.append(float(seg.mean()) - ref_level[k] if seg.size else -1e9)
        order = np.argsort(scores)[::-1]
        best = int(order[0])
        if scores[best] <= -1e8:
            continue
        if len(order) > 1 and scores[best] - scores[int(order[1])] < margin_db and prev is not None:
            w["spk"] = prev          # 차이가 작으면 앞 단어 화자를 이어 간다
        else:
            w["spk"] = tracks[idx[best]]["name"]
        prev = w["spk"]
        n += 1
    return n


def peaks(env: np.ndarray) -> str:
    """뷰어 파형: PEAK_HZ 간격 0~9 문자열(녹음 없는 곳은 '.')."""
    step = ENV_HZ // PEAK_HZ
    n = len(env) // step
    blocks = env[: n * step].reshape(n, step)
    with np.errstate(all="ignore"):
        mx = np.nanmax(np.where(np.isfinite(blocks), blocks, -np.inf), axis=1)
    out = []
    for v in mx:
        if not np.isfinite(v):
            out.append(".")
        else:
            out.append(str(int(np.clip((v + 60) / 60 * 9, 0, 9))))   # -60dB ~ 0dB
    return "".join(out)
