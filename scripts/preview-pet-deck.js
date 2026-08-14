// 반려동물 모션덱 디자인 미리보기 — 실제 쿠팡 펫 상품으로 강아지/고양이 스틸 생성.
//
//   node scripts/preview-pet-deck.js          강아지·고양이 각 1상품 스틸 생성
//
// 다음 주(8/17) 풀 생성 전에 디자인만 미리 확인하는 용도라 카피는 샘플 하드코딩.
// 결과: published/poc/pet/{dog,cat}/stills/*.jpg + deck.html (브라우저 실시간 확인용)
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import puppeteer from 'puppeteer';
import { searchProducts } from '../src/coupang/search.js';
import { buildMotionDeckHtml } from '../src/motion/deck.js';

async function toDataUri(url) {
  if (!url) return '';
  try {
    const r = await fetch(url);
    const b = Buffer.from(await r.arrayBuffer());
    return `data:${r.headers.get('content-type') || 'image/jpeg'};base64,${b.toString('base64')}`;
  } catch {
    return url;
  }
}

// 디자인 확인용 샘플 카피 (실전에서는 Claude 가 상품별로 생성)
const SAMPLES = {
  dog: {
    keyword: '강아지 장난감',
    category: '강아지 장난감·외출용품',
    hook: '산책이 신나짐',
    benefits: [
      '혼자 두어도 스스로 움직여서 놀아줍니다.',
      '충전식이라 건전지 갈 필요가 없습니다.',
      '소음이 적어 아파트에서도 부담이 없습니다.',
      '분리불안 있는 아이 에너지 발산에 좋습니다.',
    ],
    buyHook: '댕댕이 선물로 딱',
  },
  cat: {
    keyword: '고양이 장난감',
    category: '고양이 장난감·놀이용품',
    hook: '우다다 해결',
    benefits: [
      '불규칙하게 움직여서 사냥 본능을 자극합니다.',
      '자동 꺼짐 기능으로 배터리 걱정이 없습니다.',
      '깃털 리필이 포함되어 오래 쓸 수 있습니다.',
      '밤에 심심해하는 아이 야간 우다다에 좋습니다.',
    ],
    buyHook: '냥이 선물로 딱',
  },
};

const durations = [2.6, 3.2, 3.2, 3.2, 3.2, 4.2];

const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-setuid-sandbox'] });

for (const [kind, S] of Object.entries(SAMPLES)) {
  const found = await searchProducts(S.keyword, 8);
  if (!found.length) {
    console.log(`${kind}: "${S.keyword}" 검색 결과 없음 — 건너뜀`);
    continue;
  }
  const [main, ...rest] = found;
  console.log(`${kind}: ${main.productName.slice(0, 40)} (${main.productPrice}원)`);

  const [image, ...otherImages] = await Promise.all([
    toDataUri(main.productImage),
    ...rest.slice(0, 4).map((p) => toDataUri(p.productImage)),
  ]);

  const html = buildMotionDeckHtml({
    product: main, image, hook: S.hook, category: S.category,
    benefits: S.benefits, otherImages, durations, buyHook: S.buyHook,
  });

  const dir = `published/poc/pet/${kind}`;
  const stillsDir = join(dir, 'stills');
  mkdirSync(stillsDir, { recursive: true });
  writeFileSync(join(dir, 'deck.html'), html);

  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 1920 });
  await page.setContent(html, { waitUntil: 'load' });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(Array.from(document.images).map((i) =>
      i.complete ? null : new Promise((r) => { i.onload = i.onerror = r; })));
  });

  // 씬마다 완성 시점(끝-0.6s) 1장 + 표지는 등장 중(0.45s)도 1장
  let acc = 0;
  let n = 0;
  for (let s = 0; s < durations.length; s++) {
    const start = acc;
    acc += durations[s];
    const shots = s === 0 ? [['in', start + 0.45], ['full', acc - 0.6]] : [['full', acc - 0.6]];
    for (const [tag, t] of shots) {
      await page.evaluate((ms) => window.__seek(ms), t * 1000);
      const p = join(stillsDir, `${String(n++).padStart(2, '0')}-s${s}-${tag}.jpg`);
      await page.screenshot({ path: p, type: 'jpeg', quality: 92 });
    }
  }
  await page.close();
  console.log(`  → ${stillsDir} (${n}장) · ${join(dir, 'deck.html')}`);
}

await browser.close();
console.log('\n완료 — 스틸을 확인하세요.');
