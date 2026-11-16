// Question buttons of the AI 복약 상담 panel (also the questions the demo site has recorded answers for).

/** @param {object|null} res  the prediction shown on screen, or null before one */
export function suggestions(res) {
  if (!res) {
    return ["감기 기운이 있는데 어떤 약을 먹어야 하나요?", "타이레놀이랑 이부프로펜 차이가 뭐예요?", "박카스 하루에 몇 병까지 괜찮아요?",
      "약은 식후 30분에 꼭 먹어야 하나요?", "항생제는 왜 끝까지 먹어야 하나요?", "약을 깜빡하고 안 먹었으면 어떻게 하나요?"];
  }
  const out = ["이 약들 같이 먹어도 되나요?", "박카스 같이 마셔도 되나요?", "언제, 어떻게 먹는 게 좋아요?"];
  const names = (res?.products || []).map((p) => p.input);
  if (res?.summary?.verdict !== "비교적 안전") out.push("왜 위험(주의)이라고 나왔나요?");
  out.push("술 한잔은 괜찮을까요?", "어떤 증상이 생기면 병원에 가야 하나요?");
  if (names.length >= 2) out.push(`${names[0]}과 ${names[1]}은 몇 시간 간격을 둬야 하나요?`);
  if ((res?.pathway?.drugs || []).some((d) => d.release === "ER")) out.push("서방형은 왜 쪼개면 안 되나요?");
  out.push("약을 깜빡하고 안 먹었으면 어떻게 하나요?");
  return out.slice(0, 8);
}
