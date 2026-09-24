// 프리미어 흉내(mockppro.js)로 host.jsx 를 돌려 호출 순서를 JSON 으로 출력한다(pytest 가 부름).
const fs = require('fs'), vm = require('vm'), path = require('path');
const { makeMock } = require('./mockppro.cjs');
const [hostPath, jobJson, os] = process.argv.slice(2);
const m = makeMock(os || 'Windows 10');
const ctx = vm.createContext({ app: m.app, $: m.$, Sequence: m.Sequence });
vm.runInContext(fs.readFileSync(hostPath, 'utf8'), ctx);
const result = vm.runInContext('autocutMake(' + JSON.stringify(jobJson) + ')', ctx);
console.log(JSON.stringify({ result, calls: m.calls }));
