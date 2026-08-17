// 파이프라인 코어 (주간 풀 구조).
//   월: 상품 10개 풀 확정 → 평일 오전/저녁 단품 릴스 10슬롯 → 금 밤 캐러셀 5개.
//   게시는 카드/영상을 먼저 공개 URL(raw)로 올린 뒤 진행하므로 generate/publish 2단계.
import { selectWeeklyPool } from './selectProducts.js';
import { createDeeplinks } from './coupang/deeplink.js';
import { buildPostCards, buildSingleReelCards, buildStoryImage } from './cards/build.js';
import { generateCaption, generatePoolContent, buildReelCaption } from './caption.js';
import { validateCaption } from './validateCaption.js';
import { publishCarousel, publishReel } from './instagram.js';
import { buildLinkPage } from './linkPage.js';
import { buildStoryPage } from './storyPage.js';
import { buildReel } from './reel.js';
import { buildMotionReel } from './motion/renderMotionReel.js';
import { addEntry } from './archive.js';
import { appendPosted } from './postedLog.js';
import { requireEnv } from './config.js';
import { existsSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs';
import { basename } from 'node:path';
import {
  readPool, savePool, isCurrentWeek, weekKeyOf, buildSchedule, skipPastSlots,
  dueReelSlots, carouselDue, pickCarousel, markReelPosted, markCarouselPosted, nextUnpostedReel,
} from './weekPool.js';

const PUB_DIR = 'published';
const QUEUE = `${PUB_DIR}/queue.json`;

/** productUrl 에서 정확한 옵션(itemId/vendorItemId)까지 담은 쿠팡 URL 생성 */
function rawProductUrl(p) {
  try {
    const u = new URL(p.productUrl);
    const pk = u.searchParams.get('pageKey') || p.productId;
    const it = u.searchParams.get('itemId');
    const vi = u.searchParams.get('vendorItemId');
    return it && vi
      ? `https://www.coupang.com/vp/products/${pk}?itemId=${it}&vendorItemId=${vi}`
      : `https://www.coupang.com/vp/products/${pk}`;
  } catch {
    return `https://www.coupang.com/vp/products/${p.productId}`;
  }
}

/** 풀 상품 → 링크페이지/아카이브용 항목 */
function linkItem(p) {
  return {
    name: p.productName.split(',')[0].trim(),
    price: p.productPrice,
    image: p.productImage,
    copy: p.copy,
    url: p.deeplink || p.productUrl,
  };
}

/**
 * 이번 주 풀 확보 — 있으면 재사용, 없으면(새 주) 새로 생성.
 * 생성 시: 상품10 선정 → 딥링크 → 훅/카피 → 저장 + 링크페이지 갱신.
 */
export async function getOrCreatePool(now = Date.now()) {
  const existing = readPool();
  if (isCurrentWeek(existing, now)) return existing;
  return createWeekPool(now);
}

export async function createWeekPool(now = Date.now()) {
  const weekKey = weekKeyOf(now);
  const products = await selectWeeklyPool({ count: 10 });
  if (products.length < 6) {
    throw new Error(`주간 풀 상품 부족(${products.length}개) — 생성 중단`);
  }

  // 딥링크 (옵션 일치)
  let deeplinks = [];
  try {
    deeplinks = await createDeeplinks(products.map(rawProductUrl));
  } catch {
    /* 실패 시 productUrl 로 대체 */
  }
  products.forEach((p, i) => {
    p.deeplink = deeplinks[i]?.shortenUrl || p.productUrl;
  });

  // 훅/카피/해시태그 일괄 생성 후 상품에 부착
  const content = await generatePoolContent(products);
  products.forEach((p, i) => {
    p.hook = content[i].hook;
    p.copy = content[i].copy;
    p.buyHook = content[i].buyHook;
    p.narration = content[i].narration;
    p.tags = content[i].tags;
  });

  const pool = {
    weekKey,
    createdAt: new Date(now).toISOString(),
    products: products.map((p) => ({
      productId: p.productId,
      productName: p.productName,
      productPrice: p.productPrice,
      productUrl: p.productUrl,
      productImage: p.productImage,
      deeplink: p.deeplink,
      hook: p.hook,
      copy: p.copy,
      buyHook: p.buyHook,
      narration: p.narration,
      tags: p.tags,
      category: p.category,
      _score: p._score ?? 0,
    })),
    reels: buildSchedule(),
    carousel: { picks: [], posted: false, postId: null, postedAt: null },
  };
  skipPastSlots(pool, now); // 주 중간 시작 시 지난 날짜 슬롯은 건너뜀 → 오늘부터 깔끔하게
  savePool(pool);
  refreshLinkPage(pool);
  return pool;
}

/** 이번 주 풀을 링크 페이지(docs/index.html)에 반영 (허브 페이지) */
function refreshLinkPage(pool) {
  const items = pool.products.map(linkItem);
  const archive = addEntry({
    runId: pool.weekKey,
    date: pool.weekKey,
    category: '이번 주 추천템',
    products: items,
  });
  buildLinkPage(archive);
}

/**
 * 지금 게시해야 할 것들(밀린 릴스 슬롯 + 금요일 캐러셀)을 렌더 → 큐 매니페스트 기록.
 * (게시는 publishDue 에서. 그 전에 워크플로가 published/ 를 커밋해 공개 URL 확보)
 */
const STORIES_PATH = 'data/stories.json';
// 스토리는 반자동(사람이 매일 직접 올려야 함)이라 링크를 매번 알림에 실어 보낸다 —
// 텔레그램 알림 없이는 이 링크를 사용자가 다시 찾을 방법이 없었다(2026-08-17 실측).
const STORY_PAGE_URL = 'https://dannydanny11.github.io/sns-/story.html';
const kstDate = (t) => new Date(t + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
function readStories() {
  try { return JSON.parse(readFileSync(STORIES_PATH, 'utf8')); } catch { return []; }
}

export async function generateDue(now = Date.now()) {
  const pool = await getOrCreatePool(now);
  mkdirSync(PUB_DIR, { recursive: true });
  const items = [];
  const storyEntries = [];

  // ── 릴스 슬롯: 예정시각 지난 것(최대2 따라잡기). FORCE_NEXT=1 이면 시각 무관 다음 1개 강제 ──
  let reelSlots = dueReelSlots(pool, now, 2);
  if (process.env.FORCE_NEXT === '1' && reelSlots.length === 0) {
    const nx = nextUnpostedReel(pool);
    if (nx) reelSlots = [nx];
  }
  for (const slot of reelSlots) {
    const p = pool.products[slot.productIdx];
    if (!p) continue;
    // 장점 내레이션(3~4문장) — 없으면 카피로 대체
    const benefits = (p.narration && p.narration.length ? p.narration : [p.copy]).slice(0, 4);
    // 마지막 CTA용 "다른 추천템" 4개 — 슬롯별로 시작점을 돌려 다양하게
    const rest = pool.products.filter((_, idx) => idx !== slot.productIdx);
    const start = slot.slot % Math.max(rest.length, 1);
    const others = [...rest.slice(start), ...rest.slice(0, start)].slice(0, 4);

    const runId = `${pool.weekKey}-r${slot.slot}`;
    const reelPath = `${PUB_DIR}/reels/${runId}/reel.mp4`;
    const bgmPath = 'assets/reel-bgm.mp3';
    const bgm = existsSync(bgmPath) ? bgmPath : undefined;

    // 릴스 렌더 방식. 기본은 모션덱(애니메이션 HTML → 프레임 캡처).
    // 문제가 생기면 워크플로/환경변수 REEL_STYLE=legacy 로 즉시 구버전(정지 카드+줌)으로 되돌린다.
    if (process.env.REEL_STYLE === 'legacy') {
      const reelDir = `${PUB_DIR}/reels/${runId}/cards`;
      const reelCardPaths = await buildSingleReelCards(reelDir, {
        product: p, hook: p.hook, category: p.category?.name || '', benefits, others,
        buyHook: p.buyHook,
      });
      // 내레이션: 표지=훅 → 장점 슬라이드마다 1문장 → 마지막=구매 유도 (카드 수와 일치)
      const narration = [
        p.hook.replace(/\s*\n\s*/g, ' '),
        ...benefits,
        '마음에 들면 프로필 링크에서 바로 구매하세요',
      ];
      await buildReel(reelCardPaths, { outPath: reelPath, bgmPath: bgm, narration });
    } else {
      // 모션덱은 카드 JPG를 거치지 않는다(내레이션·씬 길이·오디오 믹스를 내부에서 처리).
      const r = await buildMotionReel({
        product: p, hook: p.hook, category: p.category?.name || '', benefits, others,
        buyHook: p.buyHook, outPath: reelPath, bgmPath: bgm,
      });
      console.log(
        `[reel] 모션덱 slot${slot.slot} ${r.total.toFixed(1)}s/${r.frames}프레임 ` +
        `— 캡처 ${(r.ms.capture / 1000).toFixed(0)}s, 총 ${(r.ms.totalMs / 1000).toFixed(0)}s`
      );
    }
    const caption = buildReelCaption({
      hook: p.hook, copy: p.copy, name: linkItem(p).name, url: p.deeplink, tags: p.tags,
    });
    items.push({
      type: 'reel',
      slot: slot.slot,
      productIdx: slot.productIdx,
      runId,
      reelFile: `${runId}/reel.mp4`,
      caption,
      name: linkItem(p).name,
    });

    // 반자동 스토리 카드(수동 링크스티커용) — docs/stories/ 에 렌더
    const storyFile = `${pool.weekKey}-s${slot.slot}.jpg`;
    await buildStoryImage(`docs/stories/${storyFile}`, {
      product: p, hook: p.hook, category: p.category?.name || '',
    });
    storyEntries.push({
      date: kstDate(now),
      slot: slot.slot,
      name: linkItem(p).name,
      price: p.productPrice,
      url: p.deeplink,
      image: `./stories/${storyFile}`,
    });
  }

  // 스토리 페이지 갱신(오늘분 추가, 최근 것 누적)
  if (storyEntries.length) {
    const keys = new Set(storyEntries.map((s) => `${s.date}#${s.slot}`));
    const merged = [...storyEntries, ...readStories().filter((s) => !keys.has(`${s.date}#${s.slot}`))].slice(0, 30);
    writeFileSync(STORIES_PATH, JSON.stringify(merged, null, 2) + '\n');
    buildStoryPage(merged);
  }

  // ── 금요일 캐러셀 (풀에서 5개 재구성) ──
  if (carouselDue(pool, now)) {
    const picks = pickCarousel(pool, 5);
    const chosen = picks.map((i) => ({ ...pool.products[i] }));
    // 캐러셀 캡션(묶음 소개) 생성
    const cap = await generateCaption({ category: { id: 'best', name: '이번 주 베스트', tier: 'high' }, products: chosen });
    const v = validateCaption(cap.caption);
    if (!v.ok) throw new Error(`캐러셀 캡션 검증 실패: ${v.reason}`);
    chosen.forEach((p, i) => { p.copy = cap.copies[i] || p.copy || ''; });
    const runId = `${pool.weekKey}-carousel`;
    const outDir = `${PUB_DIR}/cards/${runId}`;
    const cardPaths = await buildPostCards({ category: { name: '이번 주 베스트' }, products: chosen }, outDir, { headline: cap.headline });
    items.push({
      type: 'carousel',
      runId,
      cardFiles: cardPaths.map((p) => basename(p)),
      caption: cap.caption,
      picks,
    });
  }

  const queue = {
    weekKey: pool.weekKey,
    generatedAt: new Date(now).toISOString(),
    items,
    storyCount: storyEntries.length,
    storyPageUrl: storyEntries.length ? STORY_PAGE_URL : null,
  };
  writeFileSync(QUEUE, JSON.stringify(queue, null, 2));
  return queue;
}

export function readQueue() {
  if (!existsSync(QUEUE)) return { items: [] };
  return JSON.parse(readFileSync(QUEUE, 'utf8'));
}

/** 공개 URL 이 실제 접근 가능(200, 이미지/영상)해질 때까지 대기 (CDN 전파) */
async function waitForUrl(url, { tries = 30, intervalMs = 6000 } = {}) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { method: 'GET' });
      const ct = r.headers.get('content-type') || '';
      if (r.status === 200 && (ct.includes('image') || ct.includes('video') || ct.includes('octet-stream'))) return;
    } catch { /* 일시 오류 무시 */ }
    await new Promise((res) => setTimeout(res, intervalMs));
  }
  throw new Error(`미디어 URL 접근 대기 초과: ${url}`);
}

/**
 * 큐의 항목들을 실제 게시하고 풀 상태를 갱신.
 * @returns {Promise<{posted:Array, pool:object}>}
 */
export async function publishDue(now = Date.now()) {
  const imageBase = requireEnv('IMAGE_BASE_URL').replace(/\/$/, ''); // .../published/cards
  const pubBase = imageBase.replace(/\/cards$/, ''); // .../published
  const queue = readQueue();
  const pool = readPool();
  const posted = [];

  for (const item of queue.items) {
    try {
      if (item.type === 'reel') {
        // 이미 올린 슬롯이면 건너뜀 (중복 방지)
        const rec = pool?.reels.find((r) => r.slot === item.slot);
        if (rec?.posted) continue;
        const videoUrl = `${pubBase}/reels/${item.reelFile}`;
        await waitForUrl(videoUrl);
        const r = await publishReel({ videoUrl, caption: item.caption });
        if (pool) { markReelPosted(pool, item.slot, r.id, now); savePool(pool); }
        const p = pool?.products[item.productIdx];
        if (p) appendPosted([{ productId: p.productId, productName: p.productName }], p.category);
        posted.push({ type: 'reel', slot: item.slot, name: item.name, permalink: r.permalink, postId: r.id });
      } else if (item.type === 'carousel') {
        if (pool?.carousel?.posted) continue;
        const imageUrls = item.cardFiles.map((f) => `${imageBase}/${item.runId}/${f}`);
        await waitForUrl(imageUrls[0]);
        const r = await publishCarousel({ imageUrls, caption: item.caption });
        if (pool) {
          pool.carousel.picks = item.picks;
          markCarouselPosted(pool, r.id, now);
          savePool(pool);
        }
        const prods = (item.picks || []).map((i) => pool?.products[i]).filter(Boolean);
        if (prods.length) appendPosted(prods.map((p) => ({ productId: p.productId, productName: p.productName })), { name: '이번 주 베스트', tier: 'high' });
        posted.push({ type: 'carousel', permalink: r.permalink, postId: r.id });
      }
    } catch (e) {
      posted.push({ type: item.type, error: e.message });
    }
  }
  return { posted, pool };
}
