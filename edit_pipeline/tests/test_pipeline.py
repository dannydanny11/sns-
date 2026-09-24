import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json  # noqa: E402
import re  # noqa: E402

from autocut import cut, fcpxml, target, viewer  # noqa: E402
from autocut.pipeline import load_rules, run  # noqa: E402
from autocut.script import parse_script  # noqa: E402
from autocut.sync import estimate_offset  # noqa: E402
import make_fixture  # noqa: E402

RULES = load_rules("연수")


# ── 싱크 ──────────────────────────────────────

def _speechy(n, sr, rng):
    t = np.arange(n) / sr
    return rng.normal(size=n) * (np.sin(2 * np.pi * 0.7 * t) > 0.2)


@pytest.mark.parametrize("offset", [12.345, -5.0, 0.0])
def test_offset_recovered(offset):
    rng = np.random.default_rng(0)
    sr = 2000
    src = _speechy(sr * 200, sr, rng)
    pre = int(max(0, -offset) * sr)
    start = int(max(0, offset) * sr)
    clip = np.concatenate([0.1 * rng.normal(size=pre), 0.4 * src[start:start + 100 * sr]])
    clip = clip + 0.05 * rng.normal(size=len(clip))
    off, conf, z = estimate_offset(src, clip, sr)
    assert off == pytest.approx(offset, abs=1 / sr)
    assert conf > 0.5 and z > 20


def test_unrelated_audio_rejected():
    rng = np.random.default_rng(1)
    _, conf, _ = estimate_offset(_speechy(2000 * 120, 2000, rng), rng.normal(size=2000 * 60), 2000)
    assert conf < RULES["min_confidence"]


# ── 컷 선별 ────────────────────────────────────

@pytest.fixture
def transcript():
    return {"duration": 60.0, "words": make_fixture.fake_words()}


def test_script_mode_keeps_last_take(transcript):
    plan = cut.plan_cuts(transcript, RULES, parse_script(make_fixture.SCRIPT))
    kept = [it for it in plan["items"] if it["enabled"]]
    assert [it["sentence"] for it in kept] == [0, 1, 2, 3]
    off = {it["reason"] for it in plan["items"] if not it["enabled"]}
    assert off == {"재촬영 이전 테이크", "대본 외 발화"}
    # 두 번 읽은 마지막 문장은 나중 테이크가 남는다
    last_takes = [it for it in plan["items"] if it.get("sentence") == 3]
    assert [t["enabled"] for t in last_takes] == [False, True]
    assert plan["missing_sentences"] == []
    assert {d["reason"] for d in plan["dropped_words"]} == {"필러", "더듬음"}


def test_sentence_split_by_pause_is_one_take(transcript):
    plan = cut.plan_cuts(transcript, RULES, parse_script(make_fixture.SCRIPT))
    take = next(it for it in plan["items"] if it.get("sentence") == 2)
    assert take["enabled"] and "먼저 정하는 것입니다." in take["text"]
    assert len(take["segments"]) >= 2  # 더듬음·쉼 자리는 잘려 나간다


def test_emphasis_and_topics(transcript):
    plan = cut.plan_cuts(transcript, RULES, parse_script(make_fixture.SCRIPT))
    emph = next(it for it in plan["items"] if it["enabled"] and it.get("emphasis"))
    span = emph["emphasis"][0]
    assert span["text"] == "수업 설계의 세 가지 원칙" and span["e"] > span["s"]
    starts = [it["sentence"] for it in plan["items"] if it.get("topic_start")]
    assert starts == [0, 2]


def test_free_mode_detects_repeat(transcript):
    plan = cut.plan_cuts(transcript, RULES)
    reasons = [it["reason"] for it in plan["items"] if not it["enabled"]]
    assert reasons.count("반복(재촬영 추정)") == 2


def test_missing_sentence_reported():
    words = [w for w in make_fixture.fake_words() if w["s"] < 20]  # 마지막 문장 전에 녹음이 끊김
    plan = cut.plan_cuts({"duration": 20, "words": words}, RULES, parse_script(make_fixture.SCRIPT))
    assert plan["missing_sentences"] == [3]


# ── 기타 ──────────────────────────────────────

@pytest.mark.parametrize("value,sec", [("20m", 1200), ("90s", 90), ("1200", 1200), ("쇼츠", 59), ("1분30초", 90)])
def test_parse_target(value, sec):
    assert target.parse_target(value) == sec


def test_pathurl_windows_and_korean():
    assert fcpxml.pathurl("C:\\촬영\\a b.mp4") == "file://localhost/C:/%EC%B4%AC%EC%98%81/a%20b.mp4"


# ── 전체 흐름 ─────────────────────────────────

def test_end_to_end(tmp_path):
    fx = make_fixture.build(str(tmp_path))
    res = run(fx["project"], "연수", fx["script"], work_root=fx["work"], output_root=fx["output"], log=lambda *_: None)

    clips = {c["camera"]: c for s in res["sync"][0]["sessions"] for c in s["clips"]}
    assert clips["cam_front"]["offset"] == pytest.approx(2.5, abs=0.002)
    assert clips["cam_side"]["offset"] == pytest.approx(0.8, abs=0.002)
    assert clips["cam_tele"]["status"] == "failed"

    root = ET.parse(res["outputs"]["러프컷 XML"]).getroot()
    vclips = list(root.find(".//video").iter("clipitem"))
    assert vclips and all("cam_tele" not in c.find("file").findtext("pathurl", "") for c in vclips
                          if c.find("file").find("pathurl") is not None)
    # 셀렉츠식 멀티캠: 싱크된 카메라 2대가 트랙별로 쌓이고, 같은 컷에서는 하나만 켜짐
    assert len(root.findall(".//video/track")) == 2
    on = sorted((int(c.findtext("start")), int(c.findtext("end"))) for c in vclips if c.findtext("enabled") == "TRUE")
    assert all(a[1] == b[0] for a, b in zip(on, on[1:]))   # 켜진 클립이 빈틈 없이 이어짐
    assert int(root.findtext("sequence/duration")) == on[-1][1]
    from collections import Counter
    assert set(Counter(c.findtext("start") for c in vclips if c.findtext("enabled") == "TRUE").values()) == {1}
    # 카메라 오디오는 꺼진 채 포함, 녹음 트랙은 켜짐
    aen = {tr.find("clipitem/file").get("id"): tr.find("clipitem").findtext("enabled") for tr in root.findall(".//audio/track")}
    assert "TRUE" in aen.values() and "FALSE" in aen.values()
    assert [m.findtext("name") for m in root.iter("marker")] == ["들어가며", "첫 번째 원칙"]

    review = ET.parse(res["outputs"]["검토용 XML(탈락 테이크 포함)"]).getroot()
    assert sum(c.findtext("enabled") == "FALSE" for c in review.find(".//video").iter("clipitem")) == 3

    srt = open(res["outputs"]["대사 자막 SRT"], encoding="utf-8").read()
    assert "잠깐만요" not in srt and "살펴보겠습니다." in srt
    assert os.path.exists(res["outputs"]["포인트 자막 SRT"])


def test_viewer_and_overrides(tmp_path):
    fx = make_fixture.build(str(tmp_path))
    quiet = dict(work_root=fx["work"], output_root=fx["output"], log=lambda *_: None)
    res = run(fx["project"], "연수", fx["script"], **quiet)
    html = open(res["outputs"]["타임라인 뷰어"], encoding="utf-8").read()
    data = json.loads(re.search(r"const D = (.*?);\n", html).group(1).replace("<\\/", "</"))
    assert len(data["shots"]) == len(res["timeline"]["video"])
    assert {c["camera"] for c in data["sessions"][0]["cameras"]} == {"cam_front", "cam_side", "cam_tele"}
    for f in data["files"]:  # 뷰어가 여는 상대 경로가 실제 파일을 가리킨다
        assert os.path.exists(os.path.join(os.path.dirname(res["outputs"]["타임라인 뷰어"]), f["rel"]))

    # 뷰어에서 재촬영 이전 테이크를 살렸다고 가정하고 다시 실행
    items = res["parts"][0]["plan"]["items"]
    k = next(i for i, it in enumerate(items) if it["reason"] == "재촬영 이전 테이크")
    ov = tmp_path / "수정.json"
    ov.write_text(json.dumps({"items": {f"{res['parts'][0]['label']}#{k}": True}}), encoding="utf-8")
    res2 = run(fx["project"], "연수", fx["script"], overrides=str(ov), **quiet)
    assert res2["parts"][0]["plan"]["items"][k]["enabled"]
    assert res2["timeline"]["duration"] > res["timeline"]["duration"]
    assert viewer.apply_overrides(res2["parts"], {"없는#0": True}) == 0


# ── 통째로 넣은 촬영본 ─────────────────────────

def test_auto_sort_dump(tmp_path):
    fx = make_fixture.build_dump(str(tmp_path))
    res = run(fx["project"], work_root=fx["work"], output_root=fx["output"], log=lambda *_: None)
    sy = res["sync"][0]
    cams = {c["name"]: c for c in sy["cameras"]}
    # 카드 폴더 4개 = 카메라 4대, 캐논 분할 파일 2개와 세션을 넘나드는 파일은 같은 카메라로
    assert set(cams) == {"카드1", "측면캠", "카드3", "카드4"}
    assert cams["카드3"]["files"] == 2 and cams["카드1"]["files"] == 2
    assert cams["측면캠"]["role"] == "side" and cams["측면캠"]["evidence"] == "폴더 이름"
    assert sum(c["role"] == "front" for c in cams.values()) == 1
    # 무관한 소리의 영상은 인서트, 잡파일(.THM .LRV .XML ._*)은 무시
    assert [os.path.basename(c["file"]) for c in sy["inserts"]] == ["MVI_0015.MP4"]
    assert sy["skipped_files"] == [os.path.join("측면캠", "MISC", "info.bin")]
    # 녹음 세션 2개, 세션마다 무선 마이크 4개가 오프셋과 함께 묶임
    assert len(sy["sessions"]) == 2
    for s in sy["sessions"]:
        offs = {t["name"]: t["offset"] for t in s["reference"]["tracks"]}
        assert offs == pytest.approx({"TX01": 0.0, "TX02": 0.4, "TX03": -0.3, "TX04": 0.25}, abs=0.002)
    split = next(c for s in sy["sessions"] for c in s["clips"] if c["file"].endswith("A_0002C313A260711_101730EJ_CANON-008.MP4"))
    assert split["offset"] == pytest.approx(31.0, abs=0.01) and "분할" in split["reason"]
    assert res["preset"] == "교과서여행"
    # 화자: 가장 크게 들어온 마이크
    truth = {w["s"]: w["spk"] for ws in fx["truth"].values() for w in ws}
    words = [w for p in res["parts"] for w in p["plan"]["words"]]
    assert sum(truth[w["s"]] == w.get("spk") for w in words) / len(words) > 0.95
    # 싱크 타임라인: 카메라 4대 + 인서트 트랙, 마이크 4개 + 카메라 오디오 4개
    root = ET.parse(res["outputs"]["싱크 타임라인 XML(촬영 전체 멀티캠)"]).getroot()
    assert len(root.findall(".//video/track")) == 5
    assert len(root.findall(".//audio/track")) == 8
    rough = ET.parse(res["outputs"]["러프컷 XML"]).getroot()
    assert len(rough.findall(".//video/track")) == 4                  # 카메라 4대 멀티캠
    atr = rough.findall(".//audio/track")
    on = [all(c.findtext("enabled") == "TRUE" for c in t.findall("clipitem")) for t in atr]
    assert len(atr) == 8 and sum(on) == 4                               # 마이크 4개만 켜짐, 카메라 오디오 4개는 꺼짐


def test_roles_override(tmp_path):
    fx = make_fixture.build_dump(str(tmp_path))
    quiet = dict(work_root=fx["work"], output_root=fx["output"], until="sync", log=lambda *_: None)
    run(fx["project"], **quiet)
    res = run(fx["project"], roles="카드4=tele", **quiet)
    assert {c["role"] for s in res["sync"][0]["sessions"] for c in s["clips"] if c["camera"] == "카드4"} == {"tele"}


def test_clock_drift_corrected(tmp_path):
    from scipy.io import wavfile
    from autocut.sync import refine_offset
    sr, dur, off, ppm = 16000, 420, 5.0, 120
    rng = np.random.default_rng(3)
    t = np.arange(sr * dur) / sr
    ref = rng.normal(size=len(t)) * (np.sin(2 * np.pi * 0.37 * t) > 0)
    c_t = np.arange(sr * (dur - 20)) / sr
    clip = np.interp(off + c_t * (1 + ppm * 1e-6), t, ref)   # 기준 = off + rate × 카메라 시각
    wavfile.write(tmp_path / "ref.wav", sr, (ref * 8000).astype(np.int16))
    wavfile.write(tmp_path / "cam.wav", sr, (clip * 8000).astype(np.int16))
    o, rate = refine_offset(str(tmp_path / "ref.wav"), str(tmp_path / "cam.wav"), off + 0.01, dur - 20, dur)
    assert o == pytest.approx(off, abs=0.003)
    assert (rate - 1) * 1e6 == pytest.approx(ppm, abs=10)


def test_split_overlap_and_prefix():
    from autocut.autosort import _prefix, role_from_words, split_overlaps
    assert _prefix("A_0001C313A260711_101700EJ_CANON-008.MP4") == _prefix("A_0002C313A260711_101730EJ_CANON-008.MP4")
    assert role_from_words("B캠_측면 DCIM") == "side" and role_from_words("정면카메라") == "front"
    assert role_from_words("network") is None
    clips = [{"offset": 0, "duration": 10}, {"offset": 2, "duration": 10}, {"offset": 10.2, "duration": 5}]
    assert [len(ch) for ch in split_overlaps(clips)] == [2, 1]


def test_role_decision_from_faces():
    from autocut.visual import decide
    f = lambda fr, pr, size, people=1.0: {"frontal_rate": fr, "profile_rate": pr, "face_rate": max(fr, pr), "size": size, "people": people}  # noqa: E731
    cams = [{"name": "a", "coverage": 10, "faces": f(0.9, 0.0, 0.02)},
            {"name": "b", "coverage": 10, "faces": f(0.1, 0.8, 0.02)},
            {"name": "c", "coverage": 10, "faces": f(0.8, 0.0, 0.08)},
            {"name": "d", "coverage": 10, "faces": f(0.6, 0.0, 0.005)},
            {"name": "e", "coverage": 10, "faces": f(0.9, 0.0, 0.01, people=2.0)}]
    decide(cams)
    assert [c["role"] for c in cams] == ["front", "side", "tele", "wide", "two"]


# ── 셀렉츠 실작업 구성: 분할 녹음 + 스타일 학습 ─────────────

def _podcast(tmp_path):
    fx = make_fixture.build_podcast(str(tmp_path))
    fx["quiet"] = dict(work_root=fx["work"], output_root=fx["output"], log=lambda *_: None)
    return fx


def test_split_recording_joined(tmp_path):
    fx = _podcast(tmp_path)
    res = run(fx["project"], **fx["quiet"])
    sy = res["sync"][0]
    assert len(sy["sessions"]) == 1                                   # 마이크 1.wav·2.wav 가 한 시간축으로
    tracks = sy["sessions"][0]["reference"]["tracks"]
    by = {}
    for t in tracks:
        by.setdefault(t["name"], []).append(t["offset"])
    assert set(by) == set(make_fixture.PODCAST_SPK)                   # MIC이형1 → 이형
    assert all(sorted(v) == pytest.approx([0.0, 32.5], abs=0.01) for v in by.values())
    assert {c["name"] for c in sy["cameras"]} == {"R3", "C400", "C50"}   # 한 폴더에 섞여 있어도 파일 이름으로 구분
    offs = {c["camera"]: c["offset"] for c in sy["sessions"][0]["clips"]}
    assert offs == pytest.approx({"R3": 0.7, "C400": 1.2, "C50": 0.4}, abs=0.002)
    truth = {w["s"]: w["spk"] for w in fx["words"]}
    words = res["parts"][0]["plan"]["words"]
    assert sum(truth[w["s"]] == w.get("spk") for w in words) / len(words) > 0.95   # 2.wav 구간 화자도 맞음
    # 러프컷: 마이크 트랙 4개(두 파일이 한 트랙에 이어짐) + 카메라 오디오 3개(꺼짐)
    root = ET.parse(res["outputs"]["러프컷 XML"]).getroot()
    assert len(root.findall(".//audio/track")) == 7


def test_learn_style_roundtrip(tmp_path):
    from fractions import Fraction
    from autocut import fcpxml, learn, timeline
    fx = _podcast(tmp_path)
    sess = run(fx["project"], until="sync", **fx["quiet"])["sync"][0]["sessions"][0]
    mapping = {"이형": "C400", "은비": "C400", "시온": "C50", "다인": "R3"}
    rate = Fraction(24000, 1001)
    clips = sess["clips"]
    tracks = sess["reference"]["tracks"]
    vtr, atr, rec = {}, {}, 0
    phrases = [fx["words"][i:i + 4] for i in range(0, len(fx["words"]), 4)]
    for ph in phrases:   # 화자 규칙대로 편집한 '정답' 시퀀스
        s, e = ph[0]["s"] - 0.1, ph[-1]["e"] + 0.1
        length = timeline.to_frames(e, rate) - timeline.to_frames(s, rate)
        for cam, piece in timeline.video_pieces(clips, s, e, rec, rate, mapping[ph[0]["spk"]]):
            vtr.setdefault(cam, []).append(piece)
        for name, piece in timeline.audio_pieces(tracks, s, e, rec, rate):
            atr.setdefault(name, []).append(piece)
        rec += length
    tl = {"video": [], "audio": [], "markers": [], "duration": rec, "video_tracks": list(vtr.values()),
          "audio_tracks": [{"name": k, "clips": v} for k, v in atr.items()]}
    xml = fcpxml.write(str(tmp_path / "정답.xml"), tl, "정답", 23.976, 1920, 1080)

    res = learn.analyze(xml, media_dir=fx["project"], log=lambda *_: None)
    assert res["rules"]["speaker_cams"] == mapping
    assert res["rules"]["camera_roles"]["C400"] == "front"
    assert res["rules"]["sequence_fps"] == pytest.approx(23.976, abs=0.001)
    style = learn.save(res, str(tmp_path / "스타일.json"))

    out = run(fx["project"], style=style, **fx["quiet"])
    shots = out["parts"][0]["shots"]
    by_time = {}
    for w in out["parts"][0]["plan"]["words"]:
        for sh in shots:
            if sh["s"] <= (w["s"] + w["e"]) / 2 < sh["e"]:
                by_time[w["s"]] = (w.get("spk"), sh["camera"])
    agree = sum(mapping.get(spk) == cam for spk, cam in by_time.values()) / len(by_time)
    assert agree > 0.8                                                  # 학습한 대로 화자를 따라 앵글 전환
    root = ET.parse(out["outputs"]["러프컷 XML"]).getroot()
    assert root.findtext("sequence/rate/timebase") == "24"             # 시퀀스 23.976 도 학습대로
