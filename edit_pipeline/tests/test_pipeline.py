import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autocut import cut, fcpxml, target  # noqa: E402
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
    ends = [(int(c.findtext("start")), int(c.findtext("end"))) for c in vclips]
    assert all(a[1] == b[0] for a, b in zip(ends, ends[1:]))   # 빈틈 없이 이어짐
    assert int(root.findtext("sequence/duration")) == ends[-1][1]
    assert [m.findtext("name") for m in root.iter("marker")] == ["들어가며", "첫 번째 원칙"]

    review = ET.parse(res["outputs"]["검토용 XML(탈락 테이크 포함)"]).getroot()
    assert sum(c.findtext("enabled") == "FALSE" for c in review.find(".//video").iter("clipitem")) == 3

    srt = open(res["outputs"]["대사 자막 SRT"], encoding="utf-8").read()
    assert "잠깐만요" not in srt and "살펴보겠습니다." in srt
    assert os.path.exists(res["outputs"]["포인트 자막 SRT"])
