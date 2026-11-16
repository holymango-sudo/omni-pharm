// One-click example combinations and the food/drink/smoking chips (also what the demo site has recorded).

export const SCENARIOS = [
  { name: "타이레놀 + 와파린", drugs: ["타이레놀", "와파린"] },
  { name: "와파린 + 코다론 + 부루펜", drugs: ["쿠마딘", "코다론", "부루펜"] },
  { name: "감기약 2종 + 진통제", drugs: ["콜대원코프에스시럽", "판콜에이내복액", "타이레놀"] },
  { name: "팍스로비드 + 심바스타틴", drugs: ["팍스로비드", "심바스타틴"] },
  { name: "해외약: Doliprane + Advil + 와파린", drugs: ["Doliprane", "Advil", "와파린"] },
  { name: "일본약: ロキソニン + ワーファリン", drugs: ["ロキソニン", "ワーファリン"] },
  { name: "SSRI + 트라마돌", drugs: ["프로작", "트라마돌"] },
];

// food, drink and smoking: checked against labels and rules like any medicine
export const LIFESTYLE = [["🍺", "술"], ["🍊", "자몽주스"], ["🥛", "우유"], ["☕", "커피"], ["🥬", "녹색채소"], ["🚬", "흡연"]];
