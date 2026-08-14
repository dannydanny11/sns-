// 모션덱 릴스 — 애니메이션 타임라인 HTML 생성기 (POC).
//
// 기존 방식: 정지 카드 JPG N장 → ffmpeg 켄번스 줌 → mp4
// 이 방식  : 씬 전체가 들어있는 HTML 1장 + window.__seek(ms)로 시간을 되감을 수 있는
//            결정론적 타임라인 → Puppeteer가 프레임마다 seek+screenshot → mp4
//
// 핵심 설계:
//  · 모든 모션은 Web Animations API 로 등록 후 즉시 pause().
//    seek(ms) 는 모든 애니메이션의 currentTime 을 같은 값으로 밀어넣는다.
//    → 렌더 속도와 무관하게 프레임이 정확히 재현된다(무작위·실시간 요소 없음).
//  · delay 가 애니메이션 자체 타임라인에 포함되므로 currentTime=글로벌ms 로 충분.
//  · 등장(enter)은 fill:'both'(시작 전 = 시작값 유지 → 등장 전 숨김),
//    퇴장(exit)은 fill:'forwards'(시작 전엔 아무 영향 없음 → enter 를 덮어쓰지 않음).
//  · transform 을 두 개 이상이 동시에 건드리면 나중 것이 이겨버리므로
//    등장/켄번스/루프(둥실)는 반드시 서로 다른 중첩 엘리먼트에 건다.
const W = 1080;
const H = 1920;

function esc(s = '') {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function won(n) {
  return typeof n === 'number' ? n.toLocaleString('ko-KR') + '원' : String(n || '');
}
function cleanName(name = '') {
  return esc(String(name).split(',')[0].replace(/\|/g, ' ').trim());
}

// 같은 상품 이미지를 다른 컷처럼 보이게 하는 구도(기존 reelTemplates 의 IMG_VIEWS 계승)
const VIEWS = [
  'scale(1.0)',
  'scale(1.45) translateY(12%)',
  'scale(1.65)',
  'scale(1.45) translateY(-12%)',
  'scale(1.38) translateX(10%)',
  'scale(1.38) translateX(-10%)',
];
// 구매 유도 훅 — 상품별 buyHook 이 없을 때 쓰는 기본값(펫 종류별).
// 허위 긴급성("품절 임박")·허위 후기는 금지, "사도 좋다"는 의견 표현만.
const BUY_FALLBACK = {
  dog: ['이 가격이면 사야죠', '댕댕이 선물로 딱', '지금 사두면 이득'],
  cat: ['이 가격이면 사야죠', '냥이 선물로 딱', '지금 사두면 이득'],
  any: ['이 가격이면 사야죠', '지금 사두면 이득', '장바구니 직행각'],
};
// 씬마다 배경 blob 색을 살짝 바꿔 "같은 화면 반복" 느낌 제거 — 따뜻한 파스텔(펫 톤)
const MOODS = [
  ['#F6C089', '#F09A6E'],
  ['#BFD9C6', '#F6C089'],
  ['#F4B8B0', '#F2CE8B'],
  ['#F2CE8B', '#E8A063'],
  ['#BFD9C6', '#F4B8B0'],
  ['#F09A6E', '#BFD9C6'],
];

function hookFontSize(hook = '') {
  const n = hook.replace(/\s/g, '').length;
  if (n <= 7) return 142;
  if (n <= 10) return 120;
  if (n <= 14) return 100;
  return 84;
}

/** 훅을 글자 단위로 쪼개 순차 등장시킨다(한글은 어절보다 글자가 리듬이 좋음) */
function splitChars(text, kind = 'riseFast') {
  return [...String(text)]
    .map((ch) => {
      if (ch === '\n') return '<br>';
      if (ch === ' ') return '<span class="sp"> </span>';
      return `<span class="ch" data-anim="${kind}">${esc(ch)}</span>`;
    })
    .join('');
}

/** 본문은 어절 단위 마스크 리빌 */
function splitWords(text) {
  return String(text)
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => `<span class="wd" data-anim="mask">${esc(w)}</span>`)
    .join(' ');
}

function blobs(i) {
  const [c1, c2] = MOODS[i % MOODS.length];
  return `
    <div class="blob" data-loop="drift" style="left:-260px;top:-200px;width:900px;height:900px;background:${c1}"></div>
    <div class="blob" data-loop="drift2" style="right:-320px;bottom:-260px;width:1000px;height:1000px;background:${c2}"></div>`;
}

// ── 마스코트 3종 — 카테고리에 맞춰 자동 선택 ────────────────────────
// 강아지: 처진 귀 + 꼬리 살랑(빠름) / 고양이: 쫑긋 귀(가끔 씰룩) + 꼬리 스윙(느림)
// 중립(비-펫 카테고리): 기존 라운드 캐릭터 유지 — 이번 주 남은 옛 카테고리 릴스용.
const DOG_MASCOT = `
<svg viewBox="0 0 220 212" width="100%" height="100%">
  <ellipse cx="110" cy="200" rx="58" ry="9" fill="#00000012"/>
  <g class="tail"><path d="M176 128 q36 -4 32 -40" stroke="#E9A566" stroke-width="17" fill="none" stroke-linecap="round"/></g>
  <path d="M64 56 C42 28 18 44 30 78 C36 94 54 96 66 86z" fill="#CE8449"/>
  <path d="M156 56 C178 28 202 44 190 78 C184 94 166 96 154 86z" fill="#CE8449"/>
  <path d="M46 112 c0-38 28-64 64-64 s64 26 64 64 v18 c0 33-28 56-64 56 s-64-23-64-56z" fill="#E9A566"/>
  <ellipse cx="110" cy="138" rx="37" ry="27" fill="#FFF7EC"/>
  <g class="eyes">
    <ellipse class="eye" cx="82" cy="106" rx="9" ry="11" fill="#33241B"/>
    <ellipse class="eye" cx="138" cy="106" rx="9" ry="11" fill="#33241B"/>
  </g>
  <circle cx="85" cy="102" r="3" fill="#fff"/><circle cx="141" cy="102" r="3" fill="#fff"/>
  <circle cx="82" cy="88" r="5" fill="#C97F45"/><circle cx="138" cy="88" r="5" fill="#C97F45"/>
  <path d="M102 128 h16 c3 6-2 13-8 13 s-11-7-8-13z" fill="#4A3428"/>
  <path d="M110 141 q-8 10-17 3 M110 141 q8 10 17 3" stroke="#4A3428" stroke-width="4.5" fill="none" stroke-linecap="round"/>
  <path d="M103 147 q7 15 15 0 z" fill="#F58E9A"/>
  <circle cx="58" cy="124" r="10" fill="#F2A28C" opacity=".45"/><circle cx="162" cy="124" r="10" fill="#F2A28C" opacity=".45"/>
</svg>`;
const CAT_MASCOT = `
<svg viewBox="0 0 220 212" width="100%" height="100%">
  <ellipse cx="110" cy="200" rx="58" ry="9" fill="#00000012"/>
  <g class="tail slow"><path d="M174 138 q42 6 36 -38" stroke="#ABB2C8" stroke-width="16" fill="none" stroke-linecap="round"/></g>
  <g class="ear"><path d="M58 74 L44 20 L98 46z" fill="#ABB2C8"/><path d="M60 64 L52 32 L86 47z" fill="#F3ADB6"/></g>
  <path d="M162 74 L176 20 L122 46z" fill="#ABB2C8"/><path d="M160 64 L168 32 L134 47z" fill="#F3ADB6"/>
  <path d="M46 116 c0-38 28-62 64-62 s64 24 64 62 v16 c0 33-28 56-64 56 s-64-23-64-56z" fill="#ABB2C8"/>
  <path d="M92 62 q6 10 0 18 M110 59 q5 11 0 20 M128 62 q-6 10 0 18" stroke="#8890AC" stroke-width="6" fill="none" stroke-linecap="round"/>
  <ellipse cx="110" cy="132" rx="32" ry="23" fill="#FFFDF6"/>
  <g class="eyes">
    <ellipse class="eye" cx="80" cy="104" rx="8.5" ry="11" fill="#3E3348"/>
    <ellipse class="eye" cx="140" cy="104" rx="8.5" ry="11" fill="#3E3348"/>
  </g>
  <circle cx="83" cy="100" r="3" fill="#fff"/><circle cx="143" cy="100" r="3" fill="#fff"/>
  <path d="M104 122 h12 c2 5-2 10-6 10 s-8-5-6-10z" fill="#E58A96"/>
  <path d="M110 132 q-6 9-14 3 M110 132 q6 9 14 3" stroke="#3E3348" stroke-width="4" fill="none" stroke-linecap="round"/>
  <path d="M64 116 L34 110 M66 127 L38 132 M156 116 L186 110 M154 127 L182 132" stroke="#8890AC" stroke-width="3.5" stroke-linecap="round"/>
  <circle cx="58" cy="122" r="9" fill="#F3ADB6" opacity=".5"/><circle cx="162" cy="122" r="9" fill="#F3ADB6" opacity=".5"/>
</svg>`;
const NEUTRAL_MASCOT = `
<svg viewBox="0 0 200 210" width="100%" height="100%">
  <ellipse cx="100" cy="196" rx="54" ry="9" fill="#00000014"/>
  <path d="M100 40V18" stroke="#E0654A" stroke-width="8" stroke-linecap="round"/>
  <circle cx="100" cy="13" r="11" fill="#F2B705"/>
  <path d="M40 100c0-33 27-58 60-58s60 25 60 58v24c0 30-27 52-60 52s-60-22-60-52z" fill="#E0654A"/>
  <circle cx="60" cy="126" r="10" fill="#fff" opacity=".33"/>
  <circle cx="140" cy="126" r="10" fill="#fff" opacity=".33"/>
  <g class="eyes">
    <ellipse class="eye" cx="79" cy="108" rx="9" ry="11" fill="#2B211C"/>
    <ellipse class="eye" cx="121" cy="108" rx="9" ry="11" fill="#2B211C"/>
  </g>
  <path d="M88 132q12 11 24 0" stroke="#2B211C" stroke-width="7" fill="none" stroke-linecap="round"/>
  <g class="arm"><path d="M156 116q24-4 30-26" stroke="#E0654A" stroke-width="15" fill="none" stroke-linecap="round"/></g>
</svg>`;

// 발자국(트레일 장식용) — currentColor 로 색을 물려받는다
const PAW = `
<svg viewBox="0 0 44 40" width="100%" height="100%" fill="currentColor">
  <ellipse cx="22" cy="28" rx="10" ry="8"/>
  <circle cx="8.5" cy="18" r="5"/><circle cx="17.5" cy="12.5" r="5"/>
  <circle cx="26.5" cy="12.5" r="5"/><circle cx="35.5" cy="18" r="5"/>
</svg>`;

// 카테고리명 → 펫 종류. 비-펫 카테고리는 기존 중립 디자인으로 폴백(이번 주 잔여 슬롯 대비).
function petKindOf(category = '') {
  if (/강아지|댕댕|멍멍|퍼피/.test(category)) return 'dog';
  if (/고양이|냥이|냥집사|캣/.test(category)) return 'cat';
  return 'any';
}
const KIND_ASSETS = {
  dog: { coverEmos: ['🐶', '❤️', '🦴'], badges: ['🦴', '🐾', '🎾', '❤️', '😍', '👍'], mascot: DOG_MASCOT, paw: true },
  cat: { coverEmos: ['🐱', '❤️', '🐟'], badges: ['🐟', '🐾', '🧶', '❤️', '😻', '👍'], mascot: CAT_MASCOT, paw: true },
  any: { coverEmos: ['✨', '👀', '👍'], badges: ['✨', '👌', '🙌', '🔥', '💡', '😌'], mascot: NEUTRAL_MASCOT, paw: false },
};

/**
 * 모션덱 HTML 생성.
 * @param {object} o
 * @param {object} o.product        {productName, productPrice, ...}
 * @param {string} o.image          상품 이미지 (data URI 권장)
 * @param {string} o.hook
 * @param {string} o.category
 * @param {string[]} o.benefits     장점 문장(씬 1개당 1문장)
 * @param {string[]} o.otherImages  마지막 CTA 미니모음 이미지 4장
 * @param {number[]} o.durations    씬별 길이(초). 길이 = 1 + benefits.length + 1
 * @returns {string} HTML
 */
export function buildMotionDeckHtml(o) {
  const { product, image, hook, category = '', benefits = [], otherImages = [], durations } = o;
  const kind = petKindOf(category);
  const A = KIND_ASSETS[kind];
  const fallbacks = BUY_FALLBACK[kind];
  const buyHook =
    (o.buyHook || product.buyHook || '').trim() ||
    fallbacks[(Number(product.productId) || 0) % fallbacks.length];
  const scenes = [];
  let acc = 0;
  for (const d of durations) {
    scenes.push({ start: +acc.toFixed(3), end: +(acc + d).toFixed(3) });
    acc += d;
  }
  const total = +acc.toFixed(3);
  const nBen = benefits.length;
  const priceSceneIdx = nBen; // 마지막 장점 씬에서 가격 공개

  // ── 씬 0: 표지(훅) ────────────────────────────────────────────────
  // 발자국 트레일 — 훅 오른쪽 여백을 따라 "걸어 들어온" 듯 지그재그로 콕콕 찍힌다(펫 전용).
  // 표지는 실전에서 1.6초 남짓이라(COVER_MIN) 모든 등장을 1.1초 안에 끝낸다.
  const trail = A.paw
    ? [
        [920, 262, -18, 0.30], [856, 354, 12, 0.40], [936, 436, -8, 0.50], [872, 522, 15, 0.60],
      ].map(([x, y, r, d]) =>
        `<span class="paw" style="left:${x}px;top:${y}px;transform:rotate(${r}deg)">
           <i data-anim="pop" data-delay="${d}">${PAW}</i></span>`).join('')
    : '';
  const cover = `
<section class="scene cover">
  <div class="sbg">${blobs(0)}</div>
  ${trail}
  <div class="pad">
    <div class="chip" data-anim="fade" data-dur="0.26" data-delay="0">${A.paw ? '🐾 ' : ''}${esc(category)} · 이번 주 추천</div>
    <h1 class="hook" data-stagger="0.022" data-delay="0.01" style="font-size:${hookFontSize(hook)}px">${splitChars(hook)}</h1>
    <div class="ul" data-anim="bar" data-dur="0.42" data-delay="0.26"></div>
    <div class="frame" data-anim="imgFast" data-delay="0">
      <div class="kb" data-ken="in">
        <div class="plate"><img src="${esc(image)}"></div>
      </div>
      <span class="emo" style="left:-26px;top:34px" data-anim="pop" data-delay="0.42"><i data-loop="bob">${A.coverEmos[0]}</i></span>
      <span class="emo" style="right:-24px;top:210px" data-anim="pop" data-delay="0.54"><i data-loop="bob2">${A.coverEmos[1]}</i></span>
      <span class="emo" style="left:52px;bottom:-34px" data-anim="pop" data-delay="0.66"><i data-loop="bob">${A.coverEmos[2]}</i></span>
    </div>
  </div>
  <div class="mascot" style="right:34px;bottom:-6px;width:216px" data-anim="pop" data-delay="0.72">
    <div data-loop="bob">${A.mascot}</div>
  </div>
</section>`;

  // ── 씬 1..N: 장점 ────────────────────────────────────────────────
  const benefitScenes = benefits
    .map((b, i) => {
      const sIdx = i + 1;
      const dots = Array.from({ length: nBen }, (_, k) => `<i class="${k === i ? 'on' : ''}"></i>`).join('');
      const priceBlock =
        sIdx === priceSceneIdx
          ? `<div class="price" data-anim="rise" data-delay="0.70">
               <div class="wonrow">
                 <span class="won" data-count-to="${Number(product.productPrice) || 0}" data-count-delay="0.70" data-count-dur="0.85">${won(product.productPrice)}</span>
                 <span class="buy"><i data-loop="pulse">🛒 지금 담아두기</i></span>
               </div>
               <div class="note"><i data-loop="nudge">와우·쿠폰가는 링크에서 더 저렴 ↓</i></div>
             </div>`
          : `<div class="pname" data-anim="fade" data-delay="0.95">${cleanName(product.productName)}</div>`;
      return `
<section class="scene benefit">
  <div class="sbg">${blobs(sIdx)}</div>
  <div class="pad">
    <div class="step">
      <span class="num" data-anim="pop" data-delay="0.06">${String(sIdx).padStart(2, '0')}</span>
      <span class="dots" data-anim="fade" data-delay="0.10">${dots}</span>
    </div>
    <div class="frame sm">
      <div class="kb" data-ken="${i % 2 ? 'out' : 'in'}">
        <div class="plate"><img src="${esc(image)}" style="transform:${VIEWS[sIdx % VIEWS.length]}"></div>
      </div>
      <span class="badge" data-anim="pop" data-delay="0.60"><i data-loop="pulse">${A.badges[i % A.badges.length]}</i></span>
    </div>
    <div class="body">
      <p class="btext" data-stagger="0.042" data-delay="0.40">${splitWords(b)}</p>
    </div>
    <div class="foot">${priceBlock}</div>
  </div>
</section>`;
    })
    .join('');

  // ── 마지막 씬: CTA ───────────────────────────────────────────────
  const tiles = otherImages
    .slice(0, 4)
    .map((src) => `<div class="tile" data-anim="pop"><img src="${esc(src)}"></div>`)
    .join('');
  const cta = `
<section class="scene cta">
  <div class="sbg">${blobs(nBen + 1)}</div>
  <div class="pad">
    <div class="brand" data-anim="rise" data-delay="0.04">atoztem</div>
    <h2 class="big" data-stagger="0.045" data-delay="0.12">${splitWords(buyHook)}</h2>
    <div class="sub" data-anim="fade" data-delay="0.42">이번 주 추천템 더 있어요</div>
    <div class="go" data-anim="pop" data-delay="0.56"><i data-loop="pulse">${A.paw ? '🐾' : '👉'}</i> 프로필 링크에서 바로 구매</div>
    <div class="tiles" data-stagger="0.08" data-delay="0.72">${tiles}</div>
    <div class="disc" data-anim="fade" data-delay="1.20">
      이 게시물은 쿠팡 파트너스 활동의 일환으로,<br>이에 따른 일정액의 수수료를 제공받습니다.
    </div>
  </div>
  <div class="mascot" style="right:56px;top:250px;width:184px" data-anim="pop" data-delay="0.85">
    <div data-loop="bob">${A.mascot}</div>
  </div>
</section>`;

  const GRAIN =
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>
:root{
  /* 펫 톤 팔레트 — 따뜻한 우유빛 크림 + 살구 오렌지 + 허니 옐로 */
  --cream:#FBF2E6; --ink:#3B2E23; --sub:#A78F79;
  --accent:#E58345; --accent2:#37776B; --honey:#F2B84B; --white:#fff;
  --font:'Pretendard','Noto Sans KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif;
  /* 인스타 안전 구역 — 릴스는 위(계정명·오디오)와 아래(캡션·버튼)를 앱 UI가 덮는다.
     실측(프로필 게시물 뷰/릴스 탭 모두 대응): 위 236px, 아래 376px 는 비워 둔다. */
  --safe-top:236px; --safe-bottom:376px; --side:78px;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:${W}px;height:${H}px;background:var(--cream)}
body{font-family:var(--font);color:var(--ink);-webkit-font-smoothing:antialiased}
#stage{position:relative;width:${W}px;height:${H}px;overflow:hidden}

.scene{position:absolute;inset:0;will-change:opacity,transform,filter}
.sbg{position:absolute;inset:0;background:var(--cream);overflow:hidden}
.blob{position:absolute;border-radius:50%;filter:blur(140px);opacity:.42;will-change:transform}
.pad{position:absolute;inset:0;display:flex;flex-direction:column;
     padding:var(--safe-top) var(--side) var(--safe-bottom)}

/* 표지 */
.chip{align-self:flex-start;font-size:38px;font-weight:800;color:var(--accent);
      background:rgba(229,131,69,.14);padding:16px 34px;border-radius:999px}
.hook{margin-top:28px;font-weight:900;line-height:1.06;letter-spacing:-5px}
.hook .ch{display:inline-block;will-change:transform,opacity,filter}
.hook .sp{display:inline-block;width:.3em}
.ul{width:210px;height:14px;margin-top:26px;border-radius:999px;
    background:linear-gradient(90deg,var(--accent),var(--honey));transform-origin:left center}
/* 발자국 트레일 — 은은한 워터마크 느낌(펫 카테고리 전용) */
.paw{position:absolute;z-index:3;width:56px;height:52px;color:var(--accent);opacity:.30}
.paw i{display:block;font-style:normal;width:100%;height:100%}

.frame{position:relative;margin-top:46px;flex:1;min-height:0;will-change:transform,opacity}
.frame.sm{flex:0 0 auto;height:760px;margin-top:26px}
.cover .plate img{max-width:92%;max-height:92%}
.kb{position:absolute;inset:0;will-change:transform}
.plate{width:100%;height:100%;background:var(--white);border-radius:62px;overflow:hidden;
       display:flex;align-items:center;justify-content:center;
       box-shadow:0 44px 96px rgba(94,60,36,.17), 0 4px 12px rgba(94,60,36,.06)}
.plate img{max-width:88%;max-height:88%;object-fit:contain}

.emo{position:absolute;width:136px;height:136px;border-radius:50%;background:var(--white);
     display:flex;align-items:center;justify-content:center;font-size:68px;z-index:4;
     box-shadow:0 20px 44px rgba(94,60,36,.20)}
.emo i{font-style:normal;display:block}
.badge{position:absolute;left:-14px;bottom:-46px;width:152px;height:152px;border-radius:50%;
       background:var(--white);display:flex;align-items:center;justify-content:center;font-size:78px;z-index:4;
       box-shadow:0 22px 48px rgba(94,60,36,.20)}
.badge i{font-style:normal;display:block}
.mascot{position:absolute;z-index:5}
.mascot .eye{transform-box:fill-box;transform-origin:center}
.mascot .arm{transform-box:fill-box;transform-origin:left center}
.mascot .tail{transform-box:fill-box;transform-origin:12% 88%}
.mascot .ear{transform-box:fill-box;transform-origin:78% 92%}

/* 장점 */
.step{display:flex;align-items:center;gap:26px}
.num{font-size:56px;font-weight:900;color:var(--accent);letter-spacing:-1px;display:inline-block}
.dots{display:inline-flex;gap:12px;align-items:center}
.dots i{width:20px;height:20px;border-radius:50%;background:rgba(34,31,28,.14);display:block}
.dots i.on{background:var(--accent);width:52px;border-radius:999px}
.body{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;padding-top:30px}
.btext{font-size:62px;font-weight:900;line-height:1.32;letter-spacing:-1.6px}
.btext .wd{display:inline-block;will-change:clip-path,transform,opacity}
.foot{flex:0 0 auto;border-top:3px solid rgba(34,31,28,.09);padding-top:28px}
.pname{font-size:36px;font-weight:700;color:var(--sub);line-height:1.35;
       display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wonrow{display:flex;align-items:center;gap:26px;flex-wrap:wrap}
.price .won{font-size:94px;font-weight:900;line-height:1;letter-spacing:-3px}
.price .buy{font-size:34px;font-weight:800;color:#fff;background:var(--accent2);
            padding:16px 28px;border-radius:999px;white-space:nowrap}
.price .buy i,.price .note i{font-style:normal;display:inline-block}
.price .note{margin-top:14px;font-size:32px;font-weight:700;color:var(--sub)}

/* CTA */
.brand{font-size:42px;font-weight:900;color:var(--accent);letter-spacing:3px}
.big{margin-top:14px;font-size:96px;font-weight:900;line-height:1.14;letter-spacing:-3.5px}
.big .wd{display:inline-block}
.sub{margin-top:14px;font-size:40px;font-weight:700;color:var(--sub)}
.go{align-self:flex-start;margin-top:26px;font-size:44px;font-weight:800;color:#fff;
    background:var(--accent);padding:22px 40px;border-radius:999px;
    box-shadow:0 20px 44px rgba(224,101,74,.34)}
.go i{font-style:normal;display:inline-block}
.tiles{margin-top:38px;flex:1;min-height:0;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:22px}
.tile{background:var(--white);border-radius:34px;overflow:hidden;display:flex;align-items:center;justify-content:center;
      box-shadow:0 20px 46px rgba(94,60,36,.13)}
.tile img{width:100%;height:100%;object-fit:cover}
.disc{margin-top:28px;font-size:27px;color:var(--sub);line-height:1.55}

/* HUD */
.grain{position:absolute;inset:0;background-image:${GRAIN};opacity:.055;
       mix-blend-mode:multiply;pointer-events:none;z-index:20}
.hud{position:absolute;inset:0;z-index:15;pointer-events:none}
/* 진행바도 안전 구역 안으로 — 맨 위에 두면 계정명 오버레이에 가린다 */
.pbar{position:absolute;left:var(--side);right:var(--side);top:calc(var(--safe-top) - 48px);
      height:9px;border-radius:999px;background:rgba(34,31,28,.10);overflow:hidden}
.pbar>i{display:block;height:100%;width:0;border-radius:999px;
        background:linear-gradient(90deg,var(--accent),var(--honey))}
</style></head><body>
<div id="stage">
  <div class="scenes">${cover}${benefitScenes}${cta}</div>
  <div class="hud"><div class="pbar"><i id="prog"></i></div></div>
  <div class="grain"></div>
</div>
<script>
const SCENES = ${JSON.stringify(scenes)};
const TOTAL = ${total};
// 씬 전환 = 옆으로 빠르게 미는 슬라이드(0.26초).
// 크로스페이드(흐려지며 사라졌다 다시 나타남)는 "제품 사진이 사라진다"고 읽혀
// 그 순간 스킵을 부른다 — 슬라이드는 이미지가 화면 밖으로 나가지 않고 이어진다.
const XF = 0.26;
const EASE = 'cubic-bezier(.16,1,.3,1)';
const BACK = 'cubic-bezier(.34,1.56,.64,1)';
const anims = [];
const hooks = [];

function add(el, kf, dur, delay, opt){
  opt = opt || {};
  const a = el.animate(kf, {
    duration: Math.max(1, dur*1000), delay: Math.max(0, delay)*1000,
    easing: opt.ease || EASE, fill: opt.fill || 'both',
    iterations: opt.iter || 1, direction: opt.dir || 'normal'
  });
  a.pause(); anims.push(a); return a;
}

const KIND = {
  rise: { dur:.78, kf:[{opacity:0,transform:'translateY(86px)',filter:'blur(12px)'},
                       {opacity:1,transform:'translateY(0px)',filter:'blur(0px)'}] },
  pop:  { dur:.70, ease:BACK, kf:[{opacity:0,transform:'scale(.35)'},
                                  {opacity:1,transform:'scale(1.08)',offset:.6},
                                  {opacity:1,transform:'scale(1)'}] },
  mask: { dur:.56, kf:[{opacity:0,clipPath:'inset(-15% 100% -25% 0)',transform:'translateY(24px)'},
                       {opacity:1,clipPath:'inset(-15% 0% -25% 0)',transform:'translateY(0px)'}] },
  img:  { dur:.95, kf:[{opacity:0,transform:'scale(.86) rotate(-3.5deg) translateY(46px)'},
                       {opacity:1,transform:'scale(1) rotate(0deg) translateY(0px)'}] },
  // 표지 전용 — 첫 프레임이 곧 릴스 썸네일이자 스크롤 스토퍼다.
  // 시작값을 충분히 진하게 둬서 0프레임에도 훅과 제품이 또렷이 읽히게 하고,
  // 움직임은 "이미 있던 것이 살짝 자리를 잡는" 정도만 남긴다.
  riseFast: { dur:.32, kf:[{opacity:.55,transform:'translateY(26px)',filter:'blur(3px)'},
                           {opacity:1,transform:'translateY(0px)',filter:'blur(0px)'}] },
  imgFast:  { dur:.42, kf:[{opacity:.6,transform:'scale(.978) rotate(-1deg) translateY(12px)'},
                           {opacity:1,transform:'scale(1) rotate(0deg) translateY(0px)'}] },
  bar:  { dur:.62, kf:[{transform:'scaleX(0)'},{transform:'scaleX(1)'}] },
  fade: { dur:.60, kf:[{opacity:0},{opacity:1}] }
};

const LOOP = {
  bob:   { dur:2.2, kf:[{transform:'translateY(0px)'},{transform:'translateY(-16px)'}] },
  bob2:  { dur:2.9, kf:[{transform:'translateY(0px) rotate(0deg)'},{transform:'translateY(-13px) rotate(7deg)'}] },
  pulse: { dur:1.1, kf:[{transform:'scale(1)'},{transform:'scale(1.10)'}] },
  nudge: { dur:.9,  kf:[{transform:'translateY(0px)'},{transform:'translateY(9px)'}] },
  drift: { dur:11,  kf:[{transform:'translate(0px,0px) scale(1)'},{transform:'translate(120px,90px) scale(1.18)'}] },
  drift2:{ dur:14,  kf:[{transform:'translate(0px,0px) scale(1.1)'},{transform:'translate(-110px,-80px) scale(1)'}] }
};

function delayOf(el, s){
  const own = parseFloat(el.dataset.delay || '0');
  const st = el.closest('[data-stagger]');
  if (st && st !== el){
    const kids = Array.prototype.slice.call(st.querySelectorAll('[data-anim]'));
    return s.start + parseFloat(st.dataset.delay || '0') + kids.indexOf(el) * parseFloat(st.dataset.stagger || '.05');
  }
  return s.start + own;
}

const sceneEls = document.querySelectorAll('.scene');
sceneEls.forEach(function(sc, i){
  const s = SCENES[i];
  const last = i === sceneEls.length - 1;

  // 씬 등장/퇴장 = 좌우 슬라이드. 두 씬이 같은 속도로 움직여 필름 스트립처럼 이어진다.
  // 첫 씬은 애니메이션 없이 처음부터 제자리(첫 프레임 빈 화면 방지).
  const SLIDE = 'cubic-bezier(.45,0,.15,1)';
  if (i > 0){
    add(sc, [{transform:'translateX(100%)'},{transform:'translateX(0%)'}], XF, s.start - XF, {ease:SLIDE});
  }
  if (!last){
    add(sc, [{transform:'translateX(0%)'},{transform:'translateX(-100%)'}], XF, s.end - XF,
        {fill:'forwards', ease:SLIDE});
  }

  // 켄번스 — 씬 전체 길이에 걸친 아주 느린 확대/축소
  sc.querySelectorAll('[data-ken]').forEach(function(el){
    const zin = el.dataset.ken === 'in';
    add(el, zin ? [{transform:'scale(1)'},{transform:'scale(1.075)'}]
                : [{transform:'scale(1.075)'},{transform:'scale(1)'}],
        (s.end - s.start) + XF, Math.max(0, s.start - XF), {ease:'linear'});
  });

  // 개별 요소 등장
  sc.querySelectorAll('[data-anim]').forEach(function(el){
    const k = KIND[el.dataset.anim];
    if (!k) return;
    add(el, k.kf, parseFloat(el.dataset.dur || k.dur), delayOf(el, s), {ease:k.ease});
  });

  // 가격 카운트업
  sc.querySelectorAll('[data-count-to]').forEach(function(el){
    const to = +el.dataset.countTo;
    const at = s.start + parseFloat(el.dataset.countDelay || '0');
    const dur = parseFloat(el.dataset.countDur || '0.8');
    hooks.push(function(t){
      const p = Math.max(0, Math.min(1, (t - at) / dur));
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(to * e).toLocaleString('ko-KR') + '원';
    });
  });
});

// 루프 모션(등장 애니메이션과 충돌하지 않도록 항상 별도 엘리먼트)
document.querySelectorAll('[data-loop]').forEach(function(el){
  const l = LOOP[el.dataset.loop];
  if (!l) return;
  add(el, l.kf, l.dur, 0, {iter:Infinity, dir:'alternate', ease:'ease-in-out'});
});

// 마스코트 생명감 — 눈 깜빡임 / (중립)손 흔들기 / (강아지)꼬리 살랑 / (고양이)꼬리 스윙·귀 씰룩
document.querySelectorAll('.mascot .eye').forEach(function(el){
  add(el, [{transform:'scaleY(1)',offset:0},{transform:'scaleY(1)',offset:.93},
           {transform:'scaleY(.08)',offset:.955},{transform:'scaleY(1)',offset:.98},
           {transform:'scaleY(1)',offset:1}], 4.4, 0, {iter:Infinity, ease:'linear'});
});
document.querySelectorAll('.mascot .arm').forEach(function(el){
  add(el, [{transform:'rotate(-16deg)'},{transform:'rotate(14deg)'}], 0.62, 0,
      {iter:Infinity, dir:'alternate', ease:'ease-in-out'});
});
document.querySelectorAll('.mascot .tail').forEach(function(el){
  var slow = el.classList.contains('slow'); // 고양이는 우아하게 천천히
  add(el, [{transform:'rotate(-12deg)'},{transform:'rotate(15deg)'}], slow ? 1.7 : 0.5, 0,
      {iter:Infinity, dir:'alternate', ease:'ease-in-out'});
});
document.querySelectorAll('.mascot .ear').forEach(function(el){
  add(el, [{transform:'rotate(0deg)',offset:0},{transform:'rotate(0deg)',offset:.9},
           {transform:'rotate(-13deg)',offset:.94},{transform:'rotate(0deg)',offset:1}],
      5.2, 0, {iter:Infinity, ease:'linear'});
});

// 상단 진행바
const prog = document.getElementById('prog');
hooks.push(function(t){ prog.style.width = Math.min(100, t / TOTAL * 100) + '%'; });

window.__total = TOTAL;
window.__seek = function(ms){
  for (let i = 0; i < anims.length; i++) anims[i].currentTime = ms;
  const t = ms / 1000;
  for (let i = 0; i < hooks.length; i++) hooks[i](t);
};
window.__seek(0);
window.__ready = true;
</script></body></html>`;
}
