/*
 * 영상 자동 컷편집 — 프리미어 쪽 스크립트(ExtendScript).
 * 패널(index.html)이 부른다. 프리미어 공식 함수로 XML 을 열어 .prproj 를 만든다.
 *
 * autocutMake(작업 JSON 문자열) → "OK:..." 또는 "ERR:..."
 *   1) app.openFCPXML(러프컷 XML, .prproj 경로)   — 프리미어가 직접 변환(버전 호환 걱정 없음)
 *   2) 싱크 타임라인·검토용 XML 도 같은 프로젝트로 가져오기
 *   3) '자막' 빈에 SRT 가져오고, 러프컷 시퀀스에 화자별 캡션 트랙 만들기
 *   4) 저장
 */

function _native(p) {
    return ($.os && $.os.indexOf("Windows") !== -1) ? p.replace(/\//g, "\\") : p;
}

function _same(a, b) {
    var n = function (s) { return String(s || "").replace(/\\/g, "/").toLowerCase(); };
    return n(a) === n(b);
}

function _findProject(path) {
    try {
        for (var i = 0; i < app.projects.numProjects; i++) {
            if (_same(app.projects[i].path, path)) { return app.projects[i]; }
        }
    } catch (e) {}
    return app.project;
}

function _findSequence(proj, name) {
    var first = null;
    for (var i = 0; i < proj.sequences.numSequences; i++) {
        var s = proj.sequences[i];
        if (!first) { first = s; }
        if (s.name === name) { return s; }
    }
    return first;
}

function _childByName(bin, name) {
    for (var i = 0; i < bin.children.numItems; i++) {
        if (bin.children[i].name === name) { return bin.children[i]; }
    }
    return null;
}

function _base(p) {
    var parts = String(p).replace(/\\/g, "/").split("/");
    return parts[parts.length - 1];
}

function autocutMake(jobJson) {
    var log = [];
    try {
        var job = eval("(" + jobJson + ")");
        var prproj = _native(job.prproj);
        if (!app.openFCPXML(_native(job.rough_xml), prproj)) {
            return "ERR:러프컷 XML 을 프로젝트로 열지 못했습니다(" + _base(job.rough_xml) + ")";
        }
        var proj = _findProject(prproj);
        log.push("프로젝트 생성: " + _base(prproj));

        if (job.extra_xml && job.extra_xml.length) {
            var extra = [];
            for (var i = 0; i < job.extra_xml.length; i++) { extra.push(_native(job.extra_xml[i])); }
            try {
                proj.importFiles(extra, true, proj.rootItem, false);
                log.push("시퀀스 추가: " + extra.length + "개");
            } catch (e1) { log.push("시퀀스 추가 실패: " + e1); }
        }

        var srts = [];
        var names = [];
        if (job.speaker_captions) {
            for (var k in job.speaker_captions) {
                if (job.speaker_captions.hasOwnProperty(k)) { srts.push(_native(job.speaker_captions[k])); names.push(k); }
            }
        }
        if (!srts.length && job.captions) { srts.push(_native(job.captions)); names.push("자막"); }
        if (job.point_captions) { srts.push(_native(job.point_captions)); names.push("포인트 자막"); }

        if (srts.length) {
            var bin = proj.rootItem.createBin("자막");
            proj.importFiles(srts, true, bin, false);
            var seq = _findSequence(proj, job.project + " 러프컷");
            var made = 0;
            for (var j = 0; j < srts.length; j++) {
                var item = _childByName(bin, _base(srts[j]));
                if (!item || !seq) { continue; }
                try {
                    seq.createCaptionTrack(item, 0, Sequence.CAPTION_FORMAT_SUBTITLE);
                    made++;
                } catch (e2) {
                    log.push("캡션 트랙(" + names[j] + ") 실패: " + e2);
                }
            }
            log.push("자막 " + srts.length + "개 가져옴, 캡션 트랙 " + made + "개");
        }
        proj.save();
        log.push("저장 완료");
        return "OK:" + log.join(" / ");
    } catch (e) {
        return "ERR:" + e + (log.length ? " (" + log.join(" / ") + ")" : "");
    }
}

function autocutOpen(path) {
    try {
        app.openDocument(_native(path));
        return "OK:열림";
    } catch (e) {
        return "ERR:" + e;
    }
}

function autocutVersion() {
    return app.version;
}
