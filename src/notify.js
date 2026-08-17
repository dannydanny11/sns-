// 모듈 ⑥ 통지 — 실행 결과를 텔레그램으로 요약 전송(설정된 경우).
import { optionalEnv } from './config.js';

/**
 * 텔레그램으로 메시지 전송. 토큰 미설정 시 콘솔 출력만.
 * @param {string} message
 */
export async function notify(message) {
  const token = optionalEnv('TELEGRAM_BOT_TOKEN');
  const chatId = optionalEnv('TELEGRAM_CHAT_ID');

  console.log('\n[통지]\n' + message + '\n');

  if (!token || !chatId) return; // 미설정 시 콘솔만

  // fetch() 는 네트워크 장애(DNS·연결 실패)에만 reject 한다 — 텔레그램이 4xx 로
  // 거부해도(잘못된 chat_id, 봇 차단 등) HTTP 자체는 "정상 응답"이라 catch 에 안 걸린다.
  // 실제로 이 구멍 때문에 알림 실패가 로그에 전혀 안 남아 원인 파악이 안 됐던 적이 있어(2026-08-17),
  // 응답 본문의 ok 필드까지 확인해서 실패를 명시적으로 남긴다.
  try {
    const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: message }),
    });
    const json = await res.json().catch(() => null);
    if (!res.ok || !json?.ok) {
      console.warn(
        `텔레그램 통지 실패 (HTTP ${res.status}): ${json?.description || '(응답 본문 없음)'}`
      );
    }
  } catch (e) {
    console.warn('텔레그램 통지 실패(네트워크):', e.message);
  }
}
