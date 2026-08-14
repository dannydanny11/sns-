// 모듈 ④ 캡션 생성 (Claude API).
//   · 정보형 큐레이션 톤 ("이런 제품이 있다 / 이런 사람에게 맞다")
//   · "직접 써봤다" 류 허위 후기 표현 금지
//   · 해시태그 10~15개
//   · 대가성 문구 + CTA 는 코드에서 강제 삽입 (모델 누락 방지)
import Anthropic from '@anthropic-ai/sdk';
import { requireEnv } from './config.js';

// 대가성 필수 문구 (공정위 규정) — 반드시 포함.
export const DISCLOSURE =
  '이 게시물은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.';
export const CTA = '👉 링크는 프로필에서 확인하세요';

// 허위 후기로 오해될 수 있는 금지 표현.
const FORBIDDEN = ['직접 써', '직접 사용', '내돈내산', '제가 써보', '사용해보니', '후기'];

function productSummary(products) {
  return products
    .map((p, i) => {
      const name = p.productName.split(',')[0].trim();
      const price = p.productPrice?.toLocaleString('ko-KR');
      return `${i + 1}. ${name} (${price}원)`;
    })
    .join('\n');
}

const SYSTEM_PROMPT = `너는 한국 인스타그램 큐레이션 계정 'atoztem'의 카피라이터다.
쿠팡에서 찾은 가성비 제품을 '정보형 큐레이션' 톤으로 소개한다.

규칙(반드시 지킬 것):
- 표지 훅 문구(headline): 그날 상품들을 아우르는 **긍정적이고 설레는** 한 마디. **매번 다르게, 흥미롭게.** 8~14자, 필요하면 \\n 으로 2줄.
  · 부정어("없으면", "불편한", "힘든", "지친") 금지 → 긍정·기대·발견 톤.
  · 예: "우리 애가 좋아하는 템", "오늘의 갓성비 발견", "집사 삶의 질 5", "이건 사길 잘했다", "매일 손이 가는 템".
- 톤: "이런 제품이 있다 / 이런 사람에게 맞다" 식의 담백한 정보 제공. 과장·낚시 금지.
- 절대 금지: "직접 써봤다", "사용해보니", "내돈내산", "후기", 효능·건강 단정("통증이 사라져요") 등 허위/과장.
- 제품을 "추천"이 아니라 "소개/정리"하는 관점으로 쓴다.
- 이모지는 3~5개만 적절히. 존댓말.
- 캡션 본문은 4~7줄 이내로 간결하게.
- 해시태그 10~15개(한글 위주, # 포함).
- 릴스 첫 프레임 훅(reelHook): **아주 짧고 강한 한 방 (4~9자, 되도록 한 줄).** 스크롤을 멈추게 하는 구어체 리액션/궁금증.
  · 예: "진작 살걸", "이거 실화?", "왜 이제 알았지", "다들 이거 사더라", "이건 못 참지", "삶의 질 폭등", "집이 달라짐"
  → 긴 설명·문장 금지(짧을수록 강함). 구체적 가격 단정("다 3만원")·허위후기 금지.
- 각 제품마다 "한 줄 카피"도 만든다(14~26자): **제품 특징에 맞춰 훅 공식을 매번 다르게** 섞어라. 같은 어투 반복 금지.
  · 문제/공감형: "밥그릇 엎지르는 게 일상이라면"
  · 호기심형: "이거 하나로 발톱 걱정 끝?"
  · 특징/비교형: "세척 안 해도 되는 자동급식기"
  · 타깃 지목형: "다묘 가정 필수템"
  제품의 성격(수납·신기템·가성비·특정용도)에 맞는 공식을 골라 써라.
  → 효능·건강 단정·허위후기 금지("통증이 사라져요", "직접 써보니" X). 상황·특징·니즈는 OK.

출력은 반드시 아래 JSON 형식만:
{"headline": "캐러셀 표지 훅(긍정·흥미, \\n로 2줄 가능)", "reelHook": "릴스 첫 프레임 스크롤스토퍼 훅(\\n로 2줄 가능)", "body": "캡션 본문", "hashtags": ["#태그1", ...], "copies": ["1번상품 한줄카피(공식 다양)", ...]}`;

// ── 주간 풀: 상품별 단품 릴스용 훅·카피·내레이션·해시태그 일괄 생성 ──────
// 릴스 완주율(체류시간) 최적화 원칙 반영:
//   · 대부분 이탈은 첫 1~3초(TYPE01 훅 약함). 인사·빌드업 금지, 결과·대상부터.
//   · 자막(내레이션)이 곧 두 번째 훅 — 무음 시청자도 첫 줄로 멈추게.
//   · 본론은 결론부터, 반복 금지, 한 문장 새 정보 하나, 일상어(TYPE03).
const POOL_SYSTEM = `너는 한국 인스타그램 릴스 대본가다. 계정 'atoztem'(가성비템 큐레이션).
아래 상품 각각에 대해 **단품 릴스(20초 세로)** 대본을 만든다. 목표는 '초반 이탈을 막고 끝까지 보게' 하는 것.

릴스 완주율 원칙(반드시 지킬 것):
- 시청자는 첫 1~3초에 볼지 말지 정한다. 인사·설명·빌드업은 금지. 첫 문장부터 바로 결과/핵심.
- 자막(=내레이션 문장)이 두 번째 훅이다. 소리를 꺼도 첫 줄만으로 멈출 이유가 보여야 한다.
- 결론부터 말한다(결론→이유→상황). 같은 말을 표현만 바꿔 반복하지 않는다. 한 문장에 새 정보 하나. 어려운 말 대신 일상 단어.

각 상품마다:
- hook: 첫 프레임 스크롤스토퍼. **4~9자, 결과·변화가 보이거나 볼 사람을 지목**. 무음에도 멈출 한 방.
  · 결과형 예: "털 날림 끝", "냄새 걱정 끝", "발톱 걱정 끝", "삶의 질 폭등"
  · 지목·궁금형 예: "냥집사 필수", "이거 실화?", "왜 이제 알았지"
  · 부정어("없으면","불편") 금지 → 긍정·발견 톤. 상품 특징에 맞게 매번 다르게.
- copy: 카드용 한 줄 카피(14~26자). 훅과 다른 각도. 반복 금지.
- narration: **3~4문장(각 20~36자), 강한 순서로 배열(강→약)**.
  · 1번 문장 = 이 제품의 **가장 강한 셀링포인트를 결론부터**, 인트로 없이 즉시. (첫 자막이 곧 훅)
  · 이후 문장은 서로 다른 각도(성능·편의·사용상황·누구에게). 제품명에 드러난 실제 특징(용량·소재·기능·크기)을 근거로 구체적으로.
  · 모든 문장은 '알맹이'(정보·가치)여야 한다. 마무리 인사·CTA 문구는 넣지 마라(행동 유도는 영상 마지막 화면에서 따로 처리).
- buyHook: **영상 마지막 화면에 크게 박는 구매 유도 한마디(6~12자).** "사도 좋다 / 지금 사라"는 뉘앙스가 분명해야 한다.
  · 예: "이 가격이면 사야죠", "지금 사두면 이득", "장바구니 직행각", "살까 말까면 사기", "고민할 가격 아님"
  · 허위 긴급성("품절 임박","오늘만 이 가격") 금지 — 재고·기간을 우리가 확인할 수 없다. 의견 표현만.
- tags: 해시태그 6~9개(한글 위주, # 포함).

절대 금지: "직접 써봤다","사용해보니","내돈내산","후기" 등 허위 사용경험, 효능·건강 단정, 가격 단정 낭독, 인사말("안녕하세요"). 상황·특징·니즈 설명은 OK.

출력은 반드시 아래 JSON 배열만(상품 순서대로):
[{"hook":"...","copy":"...","buyHook":"...","narration":["문장1","문장2","문장3"],"tags":["#..",".."]}, ...]`;

/**
 * 주간 풀 상품 각각의 릴스 카피(hook/copy/tags) 일괄 생성.
 * @param {Array} products
 * @returns {Promise<Array<{hook:string, copy:string, tags:string[]}>>}
 */
export async function generatePoolContent(products) {
  const apiKey = requireEnv('ANTHROPIC_API_KEY');
  const client = new Anthropic({ apiKey });
  const list = products
    .map((p, i) => {
      const name = p.productName.split(',')[0].trim();
      const price = p.productPrice?.toLocaleString('ko-KR');
      const cat = p.category?.name || '';
      return `${i + 1}. [${cat}] ${name} (${price}원)`;
    })
    .join('\n');

  const res = await client.messages.create({
    model: 'claude-opus-5',
    max_tokens: 8000,
    output_config: { effort: 'low' },
    system: POOL_SYSTEM,
    messages: [{ role: 'user', content: `아래 ${products.length}개 상품의 릴스 카피를 만들어줘.\n\n${list}` }],
  });

  const text = res.content.filter((b) => b.type === 'text').map((b) => b.text).join('').trim();
  let arr;
  try {
    arr = JSON.parse(text.replace(/```json\s*|\s*```/g, '').trim());
  } catch {
    throw new Error(`풀 카피 JSON 파싱 실패: ${text.slice(0, 200)}`);
  }
  if (!Array.isArray(arr)) throw new Error('풀 카피 형식 오류(배열 아님)');

  // 상품 수에 맞춰 보정 + 금지어 검사
  return products.map((_, i) => {
    const it = arr[i] || {};
    const hook = String(it.hook || '오늘의 발견').trim();
    const copy = String(it.copy || '').trim();
    // 구매 유도 훅 — 모션덱 릴스 마지막 화면용. 누락돼도 렌더러가 기본값으로 대체한다.
    const buyHook = String(it.buyHook || '').trim();
    let narration = Array.isArray(it.narration) ? it.narration.map((s) => String(s).trim()).filter(Boolean) : [];
    if (narration.length === 0 && copy) narration = [copy];
    let tags = Array.isArray(it.tags) ? it.tags : [];
    const check = [hook, copy, buyHook, ...narration].join(' ');
    for (const bad of FORBIDDEN) {
      if (check.includes(bad)) throw new Error(`금지 표현 감지("${bad}") — 재생성 필요`);
    }
    tags = tags.map((h) => (String(h).startsWith('#') ? h : '#' + h)).filter((h) => h.length > 1);
    // 기본 태그 보강(최소 5개 확보 → validateCaption 통과)
    const base = ['#가성비', '#추천템', '#쿠팡추천', '#반려동물', '#꿀템'];
    for (const b of base) {
      if (tags.length >= 8) break;
      if (!tags.includes(b)) tags.push(b);
    }
    return { hook, copy, buyHook, narration, tags: tags.slice(0, 9) };
  });
}

/**
 * 단품 릴스 캡션 조립 — 그 상품 딥링크(복사용) + 프로필 안내 + 대가성 문구.
 * @param {{hook:string, copy:string, name:string, url:string, tags:string[]}} p
 */
export function buildReelCaption(p) {
  const tags = (p.tags && p.tags.length ? p.tags : ['#가성비', '#추천템', '#쿠팡추천', '#자취템', '#꿀템']).join(' ');
  const lines = [
    p.hook.replace(/\s*\n\s*/g, ' '),
    '',
    p.copy || p.name,
    '',
    `🛒 바로 구매: ${p.url}`,
    '👉 이번 주 추천템 전체는 프로필 링크',
    '',
    DISCLOSURE,
    '',
    tags,
  ];
  return lines.join('\n');
}

/**
 * 캡션 생성.
 * @param {{category:object, products:Array}} post
 * @returns {Promise<{body:string, hashtags:string[], caption:string}>}
 */
export async function generateCaption(post) {
  const apiKey = requireEnv('ANTHROPIC_API_KEY');
  const client = new Anthropic({ apiKey });
  const { category, products } = post;

  const userMsg = `카테고리: ${category.name}
아래 ${products.length}개 제품을 소개하는 인스타 캡션을 써줘.

${productSummary(products)}`;

  const res = await client.messages.create({
    model: 'claude-opus-5',
    max_tokens: 1500,
    output_config: { effort: 'low' },
    system: SYSTEM_PROMPT,
    messages: [{ role: 'user', content: userMsg }],
  });

  // 응답에서 텍스트 추출
  const text = res.content
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('')
    .trim();

  let parsed;
  try {
    // 코드펜스 등 제거 후 JSON 파싱
    const json = text.replace(/```json\s*|\s*```/g, '').trim();
    parsed = JSON.parse(json);
  } catch {
    throw new Error(`캡션 JSON 파싱 실패: ${text.slice(0, 200)}`);
  }

  let body = String(parsed.body || '').trim();
  let hashtags = Array.isArray(parsed.hashtags) ? parsed.hashtags : [];
  let copies = Array.isArray(parsed.copies) ? parsed.copies.map((c) => String(c).trim()) : [];
  let headline = String(parsed.headline || '').trim();
  let reelHook = String(parsed.reelHook || parsed.headline || '').trim();

  // 금지 표현 검사 (본문 + 카피) — 있으면 재생성 유도
  const checkText = body + ' ' + copies.join(' ');
  for (const bad of FORBIDDEN) {
    if (checkText.includes(bad)) {
      throw new Error(`금지 표현 감지("${bad}") — 재생성 필요`);
    }
  }

  // 해시태그 10~15개로 보정
  hashtags = hashtags
    .map((h) => (h.startsWith('#') ? h : '#' + h))
    .filter((h) => h.length > 1)
    .slice(0, 15);

  // 최종 캡션 조립: 본문 + CTA + 대가성 문구 + 해시태그
  const caption = [
    body,
    '',
    CTA,
    '',
    DISCLOSURE,
    '',
    hashtags.join(' '),
  ].join('\n');

  return { body, hashtags, caption, copies, headline, reelHook };
}
