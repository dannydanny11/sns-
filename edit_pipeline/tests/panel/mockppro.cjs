// 프리미어 ExtendScript API 흉내(테스트용): host.jsx 가 부르는 것만
function makeMock(os) {
  const calls = [];
  function Bin(name) { this.name = name; this.children = { numItems: 0 }; this.items = []; }
  Bin.prototype.createBin = function (n) { calls.push(['createBin', n]); const b = new Bin(n); return b; };
  Bin.prototype._add = function (item) { this.children[this.children.numItems++] = item; };
  const seqs = [];
  function Seq(name) { this.name = name; this.captions = []; }
  Seq.prototype.createCaptionTrack = function (item, t, fmt) { calls.push(['createCaptionTrack', this.name, item.name, t, fmt]); this.captions.push(item.name); return true; };
  const proj = {
    path: null, rootItem: new Bin('root'), sequences: { numSequences: 0 },
    importFiles(paths, suppress, bin, stills) {
      calls.push(['importFiles', paths.slice(), bin.name]);
      paths.forEach(p => {
        const base = p.split(/[\\/]/).pop();
        if (/\.xml$/i.test(base)) { const s = new Seq(base.replace(/\.xml$/i, '').replace(/_/g, ' ')); proj.sequences[proj.sequences.numSequences++] = s; seqs.push(s); }
        else bin._add({ name: base });
      });
      return true;
    },
    save() { calls.push(['save', proj.path]); return true; }
  };
  const app = {
    version: '25.0.0', projects: { numProjects: 0 }, project: null,
    openFCPXML(xml, prproj) {
      calls.push(['openFCPXML', xml, prproj]);
      proj.path = prproj; app.projects[app.projects.numProjects++] = proj; app.project = proj;
      const name = xml.split(/[\\/]/).pop().replace(/\.xml$/i, '');
      const s = new Seq(name.replace(/_러프컷$/, ' 러프컷')); proj.sequences[proj.sequences.numSequences++] = s; seqs.push(s);
      return true;
    },
    openDocument(p) { calls.push(['openDocument', p]); return true; }
  };
  return { app, calls, seqs, $: { os: os }, Sequence: { CAPTION_FORMAT_SUBTITLE: 'SUBTITLE' } };
}
module.exports = { makeMock };
