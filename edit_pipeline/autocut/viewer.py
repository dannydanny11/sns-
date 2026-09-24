"""셀렉츠식 타임라인 뷰어(HTML 한 파일).

- 원본 배열: 싱크 후 카메라별 클립 배치, 기준 녹음, 테이크(채택/탈락+사유)
- 러프컷: V1 샷(카메라별 색), 대사 자막, 포인트 자막, 챕터 마커
- 미리보기 재생: 원본 영상·녹음을 컷 순서대로 재생(HTML 과 원본이 같은 PC 에 있을 때)
- 테이크를 눌러 채택/탈락을 바꾸고 '수정 저장' → JSON 을 --overrides 로 넘겨 다시 실행
"""
from __future__ import annotations

import json
import os
from fractions import Fraction


def _src(path: str, out_dir: str) -> dict:
    """브라우저에서 원본을 열 경로: 상대 경로 우선, 드라이브가 다르면 절대 경로."""
    try:
        rel = os.path.relpath(path, out_dir).replace("\\", "/")
    except ValueError:  # 윈도우에서 드라이브가 다를 때
        rel = None
    from .fcpxml import pathurl
    return {"rel": rel, "abs": pathurl(path).replace("file://localhost", "file://")}


def build_data(project: str, parts: list[dict], tl: dict, rate: Fraction, caps: list[dict],
               points: list[dict], out_dir: str, proxies: dict[str, str] | None = None) -> dict:
    proxies = proxies or {}
    files: dict[str, int] = {}
    file_list: list[dict] = []

    def fid(path):
        if path not in files:
            files[path] = len(file_list)
            file_list.append({"name": os.path.basename(path), **_src(proxies.get(path, path), out_dir)})
        return files[path]

    sessions = []
    for p in parts:
        sess = p["session"]
        ref = sess["reference"]
        cams = {}
        for c in sess["clips"]:
            cams.setdefault(c["camera"], {"camera": c["camera"], "role": c["role"], "clips": []})
            cams[c["camera"]]["clips"].append({
                "file": fid(c["file"]), "s": c["offset"], "e": (c["offset"] + c["duration"]) if c["offset"] is not None else None,
                "status": c["status"], "reason": c["reason"], "confidence": c["confidence"], "dur": c["duration"]})
        items = []
        for k, it in enumerate(p["plan"]["items"]):
            items.append({"id": f"{p['label']}#{k}", "s": it["s"], "e": it["e"], "enabled": it["enabled"],
                          "reason": it["reason"], "text": it["text"], "script": it.get("script"),
                          "sentence": it.get("sentence"), "topic": bool(it.get("topic_start")),
                          "segments": it["segments"], "emphasis": it.get("emphasis", [])})
        sessions.append({"label": p["label"], "place": p["place"], "duration": ref["duration"],
                         "ref": {"file": fid(ref["file"]), "source": ref.get("source", "recorder")},
                         "cameras": list(cams.values()), "items": items})

    fr = float(rate)
    shots = []
    for v, a in zip(tl["video"], tl["audio"]):
        shots.append({"s": v["start"] / fr, "e": v["end"] / fr, "camera": v["camera"], "role": v["role"],
                      "file": fid(v["file"]), "in": v["in"] / fr, "why": v["why"],
                      "ref_file": fid(a["file"]), "ref_in": a["in"] / fr})
    return {
        "project": project, "fps": fr, "duration": tl["duration"] / fr, "files": file_list,
        "sessions": sessions, "shots": shots,
        "markers": [{"t": m["frame"] / fr, "name": m["name"]} for m in tl["markers"]],
        "captions": caps, "points": points,
    }


def write(path: str, data: dict) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__TITLE__", data["project"]).replace("__DATA__", payload)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def load_overrides(path: str) -> dict[str, bool]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: bool(v) for k, v in data.get("items", {}).items()}


def apply_overrides(parts: list[dict], overrides: dict[str, bool]) -> int:
    n = 0
    for p in parts:
        for k, it in enumerate(p["plan"]["items"]):
            key = f"{p['label']}#{k}"
            if key in overrides and overrides[key] != it["enabled"]:
                it["enabled"] = overrides[key]
                it["reason"] = "" if it["enabled"] else "수동 제외"
                n += 1
    return n


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ 타임라인</title>
<style>
:root{
  --bg:#141518; --panel:#1c1d21; --panel2:#23252a; --line:#2e3036; --text:#e7e8ea; --dim:#9a9ca3; --faint:#6b6e76;
  --accent:#f5c451; --kept:#3fb97a; --off:#c9534f; --off-bg:#3a2324; --play:#ff5b4f;
  --front:#4c8df6; --side:#2fb6a8; --tele:#e0a13a; --wide:#9b7cf2; --two:#e2699b; --other:#8a8f99;
  --lane:44px; --label:112px;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);font:13px/1.4 "Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
button,select,input{font:inherit;color:inherit}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:15px;margin:0 8px 0 0;font-weight:650;letter-spacing:-.01em}
.seg{display:inline-flex;background:var(--panel2);border-radius:8px;padding:2px}
.seg button{border:0;background:none;padding:5px 12px;border-radius:6px;cursor:pointer;color:var(--dim)}
.seg button.on{background:var(--line);color:var(--text)}
select{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
.stats{margin-left:auto;color:var(--dim);font-variant-numeric:tabular-nums}
.stats b{color:var(--text);font-weight:600}
main{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(260px,1fr);gap:12px;padding:12px 16px}
.monitor{background:#000;border-radius:10px;overflow:hidden;position:relative;aspect-ratio:16/9}
.monitor video{width:100%;height:100%;display:block;object-fit:contain;background:#000}
.monitor .cap{position:absolute;left:0;right:0;bottom:7%;text-align:center;pointer-events:none}
.monitor .cap span{background:rgba(0,0,0,.72);padding:4px 10px;border-radius:4px;font-size:clamp(13px,2vw,20px)}
.monitor .point{position:absolute;left:6%;top:8%;pointer-events:none}
.monitor .point span{background:var(--accent);color:#1b1b1b;font-weight:700;padding:6px 12px;border-radius:6px;font-size:clamp(13px,2.1vw,22px)}
.monitor .tag{position:absolute;left:10px;top:10px;font-size:11px;background:rgba(0,0,0,.6);padding:2px 8px;border-radius:4px}
.monitor .nomedia{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;color:var(--dim);padding:24px}
.side{background:var(--panel);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:10px;min-height:0}
.side h2{font-size:12px;color:var(--dim);font-weight:600;margin:0;text-transform:none}
.detail{background:var(--panel2);border-radius:8px;padding:10px;min-height:90px}
.detail .t{font-size:14px;margin-top:4px}
.detail .m{color:var(--dim);font-size:12px;margin-top:6px}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;margin-right:4px}
.pill.k{background:rgba(63,185,122,.18);color:var(--kept)} .pill.o{background:rgba(201,83,79,.2);color:#ee8b87}
.changes{flex:1;overflow:auto;font-size:12px;color:var(--dim)}
.changes div{padding:3px 0;border-bottom:1px solid var(--line)}
.btn{background:var(--accent);color:#1b1b1b;border:0;border-radius:7px;padding:7px 12px;font-weight:650;cursor:pointer}
.btn[disabled]{opacity:.4;cursor:default}
.btn.ghost{background:var(--panel2);color:var(--text);font-weight:500}
.transport{display:flex;align-items:center;gap:10px;padding:0 16px 8px;flex-wrap:wrap}
.transport .btn,.legend span,.transport label{white-space:nowrap}
.transport .time{font-variant-numeric:tabular-nums;color:var(--dim);min-width:150px}
.transport input[type=range]{width:140px;accent-color:var(--accent)}
.legend{display:flex;gap:10px;margin-left:auto;color:var(--dim);font-size:12px;flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.tl{margin:0 16px 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel);display:grid;grid-template-columns:var(--label) 1fr;overflow:hidden}
.labels{border-right:1px solid var(--line);background:var(--panel)}
.labels div{height:var(--lane);display:flex;align-items:center;padding:0 10px;border-bottom:1px solid var(--line);font-size:12px;color:var(--dim);gap:6px;cursor:default}
.labels div.ruler{height:26px}
.labels div.mon{cursor:pointer}
.labels div.mon.on{color:var(--text)}
.labels div.mon.on::after{content:"모니터";margin-left:auto;font-size:10px;color:var(--accent)}
.scroll{overflow-x:auto;overflow-y:hidden;position:relative}
.canvas{position:relative}
.lane{height:var(--lane);border-bottom:1px solid var(--line);position:relative}
.ruler{height:26px;position:relative;border-bottom:1px solid var(--line);cursor:pointer}
.ruler span{position:absolute;top:6px;font-size:10px;color:var(--faint);transform:translateX(3px);font-variant-numeric:tabular-nums}
.ruler i{position:absolute;bottom:0;width:1px;height:6px;background:var(--line)}
.clip{position:absolute;top:5px;bottom:5px;border-radius:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:3px 6px;font-size:11px;color:#fff;cursor:pointer;border:1px solid rgba(255,255,255,.08)}
.clip:hover{filter:brightness(1.15)}
.clip.sel{outline:2px solid var(--accent);outline-offset:-1px}
.clip.failed{background:repeating-linear-gradient(45deg,#3b3d44,#3b3d44 6px,#2d2f35 6px,#2d2f35 12px)!important;color:var(--dim)}
.take.kept{background:rgba(63,185,122,.28);border-color:rgba(63,185,122,.6);color:#d9f5e6}
.take.off{background:var(--off-bg);border-color:rgba(201,83,79,.55);color:#f0b5b2}
.take.changed{box-shadow:inset 0 0 0 2px var(--accent)}
.seg-in{position:absolute;top:0;bottom:0;background:rgba(63,185,122,.35)}
.capc{background:#3a3c43;color:var(--text)}
.ptc{background:var(--accent);color:#1b1b1b;font-weight:600}
.ref{background:#3f4450}
.marker{position:absolute;top:4px;font-size:11px;color:var(--accent);white-space:nowrap;transform:translateX(-1px);border-left:2px solid var(--accent);padding-left:4px;height:calc(100% - 8px)}
.wave{position:absolute;inset:0;pointer-events:none}
.playhead{position:absolute;top:0;bottom:0;width:2px;background:var(--play);pointer-events:none;z-index:5}
.playhead::before{content:"";position:absolute;top:0;left:-5px;border:6px solid transparent;border-top-color:var(--play)}
@media (max-width:820px){main{grid-template-columns:1fr} :root{--label:84px} .legend{margin-left:0;width:100%} .labels div.mon.on::after{content:"●"}}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="seg" id="views"><button data-v="rough" class="on">러프컷</button><button data-v="source">원본 배열</button></div>
  <select id="sess" hidden></select>
  <div class="stats" id="stats"></div>
</header>
<main>
  <div>
    <div class="monitor">
      <video id="vid" muted playsinline preload="auto"></video>
      <audio id="aud" preload="auto"></audio>
      <div class="tag" id="tag"></div>
      <div class="point" id="point"></div>
      <div class="cap" id="cap"></div>
      <div class="nomedia" id="nomedia">원본 영상을 열 수 없습니다.<br>촬영본이 있는 PC 에서 열었는지 확인하고, MXF·HEVC 처럼 브라우저가 못 여는 형식이면<br><code>--proxy</code> 를 붙여 다시 실행하세요.</div>
    </div>
  </div>
  <div class="side">
    <h2>선택</h2>
    <div class="detail" id="detail"><span style="color:var(--dim)">타임라인의 클립이나 테이크를 누르세요. 원본 배열에서 테이크를 두 번 누르면 채택/탈락이 바뀝니다.</span></div>
    <h2>수정한 테이크</h2>
    <div class="changes" id="changes"></div>
    <div style="display:flex;gap:8px">
      <button class="btn" id="save" disabled>수정 저장(JSON)</button>
      <button class="btn ghost" id="reset" disabled>되돌리기</button>
    </div>
    <div style="color:var(--faint);font-size:11px">저장한 파일을 <code>--overrides</code> 로 넘겨 다시 실행하면 XML·자막에 반영됩니다.</div>
  </div>
</main>
<div class="transport">
  <button class="btn ghost" id="playbtn">▶ 재생</button>
  <span class="time" id="time"></span>
  <label style="color:var(--dim)">확대 <input type="range" id="zoom" min="2" max="200" value="20"></label>
  <div class="legend" id="legend"></div>
</div>
<div class="tl">
  <div class="labels" id="labels"></div>
  <div class="scroll" id="scroll"><div class="canvas" id="canvas"></div></div>
</div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const ROLE = r => ["front","side","tele","wide","two"].includes(r) ? r : "other";
const col = r => getComputedStyle(document.documentElement).getPropertyValue("--" + ROLE(r)).trim();
const fmt = t => { t = Math.max(0, t); const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return (h ? h + ":" + String(m).padStart(2,"0") : m) + ":" + s.toFixed(1).padStart(4,"0"); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let view = "rough", sessIdx = 0, pps = 20, t = 0, playing = false, lastTick = 0;
let monitorCam = null, selected = null;
const original = {}; D.sessions.forEach(s => s.items.forEach(it => original[it.id] = it.enabled));
const changed = {};

$("#title").textContent = D.project;
D.sessions.forEach((s, i) => $("#sess").add(new Option(s.label.replace("__", " · "), i)));
document.querySelectorAll("#views button").forEach(b => b.onclick = () => {
  view = b.dataset.v; document.querySelectorAll("#views button").forEach(x => x.classList.toggle("on", x === b));
  $("#sess").hidden = view !== "source" || D.sessions.length < 2; t = 0; stop(); fit(); render(); });
$("#sess").onchange = e => { sessIdx = +e.target.value; t = 0; stop(); fit(); render(); };
function fit() { pps = Math.max(2, Math.min(200, Math.round((($("#scroll").clientWidth || 900) - 40) / Math.max(10, total()))));
  $("#zoom").value = pps; $("#scroll").scrollLeft = 0; }
$("#zoom").oninput = e => { const center = t; pps = +e.target.value; render();
  $("#scroll").scrollLeft = Math.max(0, center * pps - $("#scroll").clientWidth / 2); };

function total() { return view === "rough" ? D.duration : D.sessions[sessIdx].duration; }
function sess() { return D.sessions[sessIdx]; }

function legend() {
  const roles = new Set(); D.sessions.forEach(s => s.cameras.forEach(c => roles.add(ROLE(c.role))));
  const names = {front:"정면",side:"측면",tele:"망원",wide:"와이드",two:"2인",other:"기타"};
  $("#legend").innerHTML = [...roles].map(r => `<span><i style="background:${col(r)}"></i>${names[r]}</span>`).join("")
    + `<span><i style="background:var(--kept)"></i>채택</span><span><i style="background:var(--off)"></i>탈락</span>`;
}

function stats() {
  const all = D.sessions.flatMap(s => s.items), kept = all.filter(i => i.enabled).length;
  const src = D.sessions.reduce((a, s) => a + s.duration, 0);
  $("#stats").innerHTML = `원본 <b>${fmt(src)}</b> → 러프컷 <b>${fmt(D.duration)}</b> · 샷 <b>${D.shots.length}</b> · 테이크 채택 <b>${kept}</b>/${all.length}`;
}

function el(tag, cls, css, html) { const e = document.createElement(tag); if (cls) e.className = cls;
  if (css) Object.assign(e.style, css); if (html != null) e.innerHTML = html; return e; }
function box(s, e) { return {left: (s * pps) + "px", width: Math.max(2, (e - s) * pps - 1) + "px"}; }

function ruler(width) {
  const r = el("div", "ruler"); r.style.width = width + "px";
  const step = [1,2,5,10,15,30,60,120,300,600].find(x => x * pps >= 70) || 600;
  for (let s = 0; s <= total(); s += step) { r.append(el("i", "", {left: s*pps + "px"})); r.append(el("span", "", {left: s*pps + "px"}, fmt(s).replace(/\.\d$/, ""))); }
  r.onclick = ev => seek((ev.offsetX + (ev.target === r ? 0 : ev.target.offsetLeft)) / pps);
  return r;
}

function lane(label, extra) { const l = el("div", "lane"); l.dataset.label = label; if (extra) Object.assign(l.dataset, extra); return l; }

function render() {
  const width = Math.ceil(total() * pps) + 40, canvas = $("#canvas"), labels = $("#labels");
  canvas.innerHTML = ""; labels.innerHTML = ""; canvas.style.width = width + "px";
  labels.append(el("div", "ruler", null, "")); canvas.append(ruler(width));
  const lanes = [];
  if (view === "rough") {
    const mk = lane("마커");
    D.markers.forEach(m => mk.append(el("div", "marker", {left: m.t * pps + "px"}, esc(m.name))));
    lanes.push(mk);
    const v1 = lane("V1 영상");
    D.shots.forEach((sh, i) => { const c = el("div", "clip", {...box(sh.s, sh.e), background: col(sh.role)},
        esc(sh.camera.replace(/^cam_/, "")) + (sh.why ? " · " + esc(sh.why) : ""));
      c.title = `${sh.camera} ${fmt(sh.s)}–${fmt(sh.e)}`; c.onclick = () => select({type:"shot", i}, c); v1.append(c); });
    lanes.push(v1);
    const cl = lane("자막");
    D.captions.forEach(cp => { const c = el("div", "clip capc", box(cp.s, cp.e), esc(cp.text)); c.title = cp.text; c.onclick = () => seek(cp.s); cl.append(c); });
    lanes.push(cl);
    if (D.points.length) { const pl = lane("포인트 자막");
      D.points.forEach(p => { const c = el("div", "clip ptc", box(p.s, p.e), esc(p.text)); c.onclick = () => seek(p.s); pl.append(c); });
      lanes.push(pl); }
    const a1 = lane("A1 녹음");
    D.shots.forEach(sh => a1.append(el("div", "clip ref", box(sh.s, sh.e), "")));
    lanes.push(a1);
  } else {
    const S = sess();
    if (!monitorCam || !S.cameras.some(c => c.camera === monitorCam)) monitorCam = (S.cameras.find(c => c.role === "front" && c.clips.some(k => k.status === "ok")) || S.cameras.find(c => c.clips.some(k => k.status === "ok")) || {}).camera;
    S.cameras.forEach(cam => { const l = lane(cam.camera.replace(/^cam_/, ""), {cam: cam.camera});
      cam.clips.forEach(k => {
        if (k.status !== "ok") { const c = el("div", "clip failed", {left: "4px", width: Math.max(80, (k.dur || 10) * pps) + "px"}, "싱크 실패 · " + esc(D.files[k.file].name));
          c.title = k.reason; c.onclick = () => select({type:"camclip", cam, k}, c); l.append(c); return; }
        const c = el("div", "clip", {...box(k.s, k.e), background: col(cam.role)}, esc(D.files[k.file].name));
        c.title = `offset ${k.s.toFixed(3)}s · 신뢰도 ${k.confidence}`; c.onclick = () => select({type:"camclip", cam, k}, c); l.append(c); });
      lanes.push(l); });
    const rl = lane("녹음(기준)");
    rl.append(el("div", "clip ref", box(0, S.duration), esc(D.files[S.ref.file].name)));
    lanes.push(rl);
    const tk = lane("테이크");
    S.items.forEach(it => {
      const c = el("div", "clip take " + (it.enabled ? "kept" : "off") + (changed[it.id] !== undefined ? " changed" : ""), box(it.s, it.e));
      if (it.enabled) it.segments.forEach(g => c.append(el("div", "seg-in", {left: (g.s - it.s) * pps + "px", width: Math.max(1, (g.e - g.s) * pps) + "px"})));
      c.append(document.createTextNode((it.topic ? "◆ " : "") + (it.script || it.text)));
      c.title = (it.enabled ? "채택" : "탈락: " + it.reason) + "\n" + it.text;
      c.dataset.id = it.id;
      c.onclick = () => select({type:"take", it}, c);
      c.ondblclick = () => toggleTake(it);
      tk.append(c); });
    lanes.push(tk);
  }
  lanes.forEach(l => { canvas.append(l);
    const lab = el("div", l.dataset.cam ? "mon" + (l.dataset.cam === monitorCam ? " on" : "") : "", null, esc(l.dataset.label));
    if (l.dataset.cam) lab.onclick = () => { monitorCam = l.dataset.cam; render(); show(); };
    labels.append(lab); });
  canvas.append(el("div", "playhead", {left: t * pps + "px"}));
  canvas.onclick = ev => { if (ev.target === canvas || ev.target.classList.contains("lane")) seek((ev.clientX - canvas.getBoundingClientRect().left) / pps); };
  stats(); show();
}

function select(obj, node) {
  document.querySelectorAll(".clip.sel").forEach(x => x.classList.remove("sel")); node.classList.add("sel"); selected = obj;
  let h = "";
  if (obj.type === "shot") { const sh = D.shots[obj.i]; seek(sh.s);
    h = `<span class="pill k">${esc(sh.camera)}</span>${sh.why ? esc(sh.why) : "기본 배치"}<div class="t">${fmt(sh.s)} – ${fmt(sh.e)} (${(sh.e - sh.s).toFixed(1)}초)</div><div class="m">${esc(D.files[sh.file].name)} · 원본 ${fmt(sh.in)}부터</div>`; }
  else if (obj.type === "camclip") { const k = obj.k;
    h = `<span class="pill ${k.status === "ok" ? "k" : "o"}">${esc(obj.cam.camera)}</span>${esc(D.files[k.file].name)}<div class="m">${k.status === "ok" ? `오프셋 ${k.s.toFixed(3)}초 · 신뢰도 ${k.confidence} · 길이 ${fmt(k.dur)}` : "제외 사유: " + esc(k.reason)}</div>`; }
  else if (obj.type === "take") { const it = obj.it; seek(it.s);
    h = `<span class="pill ${it.enabled ? "k" : "o"}">${it.enabled ? "채택" : "탈락"}</span>${changed[it.id] !== undefined ? "직접 변경" + (it.reason ? " · 원래 사유: " + esc(it.reason) : "") : esc(it.reason || "")}${it.sentence != null ? ` · 대본 ${it.sentence + 1}번 문장` : ""}<div class="t">${esc(it.text)}</div>${it.script && it.script !== it.text ? `<div class="m">대본: ${esc(it.script)}</div>` : ""}<div class="m">원본 ${fmt(it.s)} – ${fmt(it.e)}</div><button class="btn ghost" style="margin-top:8px" id="tog">${it.enabled ? "탈락시키기" : "채택하기"}</button>`; }
  $("#detail").innerHTML = h;
  if (obj.type === "take") $("#tog").onclick = () => toggleTake(obj.it);
}

function toggleTake(it) {
  it.enabled = !it.enabled;
  if (it.enabled === original[it.id]) delete changed[it.id]; else changed[it.id] = it.enabled;
  refreshChanges(); render();
  const node = document.querySelector(`.take[data-id="${CSS.escape(it.id)}"]`); if (node) select({type:"take", it}, node);
}

function refreshChanges() {
  const ids = Object.keys(changed), all = Object.fromEntries(D.sessions.flatMap(s => s.items).map(i => [i.id, i]));
  $("#changes").innerHTML = ids.length ? ids.map(id => `<div>${changed[id] ? "＋ 채택" : "－ 제외"} · ${esc(all[id].text.slice(0, 40))}</div>`).join("") : "없음";
  $("#save").disabled = $("#reset").disabled = !ids.length;
}
$("#save").onclick = () => {
  const blob = new Blob([JSON.stringify({project: D.project, items: changed}, null, 1)], {type: "application/json"});
  const a = el("a"); a.href = URL.createObjectURL(blob); a.download = D.project + "_수정.json"; a.click(); URL.revokeObjectURL(a.href); };
$("#reset").onclick = () => { D.sessions.forEach(s => s.items.forEach(it => it.enabled = original[it.id]));
  Object.keys(changed).forEach(k => delete changed[k]); refreshChanges(); render(); };

// ── 재생 ────────────────────────────────
const vid = $("#vid"), aud = $("#aud");
let curV = null, curA = null;
function srcOf(i) { const f = D.files[i]; return f.rel || f.abs; }
function load(elm, fileIdx, time) {
  const f = D.files[fileIdx];
  if (elm.dataset.file !== String(fileIdx)) {
    elm.dataset.file = fileIdx; elm.dataset.tried = "rel"; elm.src = f.rel || f.abs;
    elm.onerror = () => { if (elm.dataset.tried === "rel" && f.abs) { elm.dataset.tried = "abs"; elm.src = f.abs; }
      else $("#nomedia").style.display = "flex"; };
  }
  elm.onloadeddata = () => { $("#nomedia").style.display = "none"; };
  if (Math.abs(elm.currentTime - time) > (playing ? 0.25 : 0.02)) { try { elm.currentTime = time; } catch (e) {} }
}
function at() {
  if (view === "rough") { const sh = D.shots.find(s => t >= s.s && t < s.e) || D.shots[D.shots.length - 1];
    if (!sh) return null; const d = t - sh.s;
    return {v: sh.file, vt: sh.in + d, a: sh.ref_file, at: sh.ref_in + d, cam: sh.camera, why: sh.why, key: sh}; }
  const S = sess(), cam = S.cameras.find(c => c.camera === monitorCam);
  const k = cam && cam.clips.find(k => k.status === "ok" && t >= k.s && t < k.e);
  return {v: k ? k.file : null, vt: k ? t - k.s : 0, a: S.ref.file, at: t, cam: k ? monitorCam : monitorCam + " (촬영 없음)", why: ""};
}
function overlay() {
  const list = view === "rough" ? D.captions : [], pts = view === "rough" ? D.points : [];
  const cp = list.find(c => t >= c.s && t < c.e), pt = pts.find(p => t >= p.s && t < p.e);
  $("#cap").innerHTML = cp ? `<span>${esc(cp.text)}</span>` : "";
  $("#point").innerHTML = pt ? `<span>${esc(pt.text)}</span>` : "";
  if (view === "source") { const it = sess().items.find(i => t >= i.s && t < i.e);
    $("#cap").innerHTML = it ? `<span style="${it.enabled ? "" : "color:#f0b5b2"}">${it.enabled ? "" : "[탈락] "}${esc(it.text)}</span>` : ""; }
}
function show() {
  const p = at(); $("#time").textContent = fmt(t) + " / " + fmt(total());
  document.querySelector(".playhead") && (document.querySelector(".playhead").style.left = t * pps + "px");
  overlay(); if (!p) return;
  $("#tag").textContent = (p.cam || "").replace(/^cam_/, "") + (p.why ? " · " + p.why : "");
  if (p.v != null) { vid.style.visibility = "visible"; load(vid, p.v, p.vt); } else vid.style.visibility = "hidden";
  load(aud, p.a, p.at);
}
function seek(x) { t = Math.min(Math.max(0, x), total()); show(); follow(); }
function follow() { const sc = $("#scroll"), x = t * pps; if (x < sc.scrollLeft || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = x - 80; }
function tick(now) { if (!playing) return; const dt = (now - lastTick) / 1000; lastTick = now; t += dt;
  if (t >= total()) { t = total(); stop(); } show(); follow(); requestAnimationFrame(tick); }
function play() { playing = true; lastTick = performance.now(); vid.play().catch(() => {}); aud.play().catch(() => {});
  $("#playbtn").textContent = "❚❚ 정지"; requestAnimationFrame(tick); }
function stop() { playing = false; vid.pause(); aud.pause(); $("#playbtn").textContent = "▶ 재생"; }
$("#playbtn").onclick = () => playing ? stop() : play();
document.addEventListener("keydown", e => { if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); playing ? stop() : play(); }
  if (e.code === "ArrowLeft") seek(t - (e.shiftKey ? 5 : 1)); if (e.code === "ArrowRight") seek(t + (e.shiftKey ? 5 : 1)); });

fit(); legend(); refreshChanges(); render();
</script>
</body>
</html>
"""
