// Patient presets and the request body the server receives. Plain JS (no JSX) so the demo recorder
// (scripts/record-demo.mjs) builds exactly the same requests as the screens.

// Child-Pugh inputs (used only when a liver disease is selected): albumin g/dL, INR, ascites, encephalopathy, or the class
const LIVER0 = { albumin: 4.0, inr: 1.0, ascites: "none", encephalopathy: "none", child_pugh: null };
export const PRESETS = {
  "간경변 고령": { age: 74, sex: "M", weight_kg: 61, height_cm: 168, labMode: "scr", scr: 1.1, egfr: 70, ast: 128, alt: 96,
    total_bilirubin: 3.4, ...LIVER0, albumin: 2.9, inr: 1.6, ascites: "mild",
    conditions: ["hepatic", "arrhythmia"], diagnosis: "", symptoms: "무릎 관절통" },
  "녹내장·전립선 노인": { age: 78, sex: "M", weight_kg: 58, height_cm: 163, labMode: "scr", scr: 1.3, egfr: 55, ast: 24,
    alt: 20, total_bilirubin: 0.8, ...LIVER0, conditions: ["glaucoma", "bph", "hypertension"], diagnosis: "", symptoms: "감기·콧물" },
  "만성콩팥병": { age: 68, sex: "F", weight_kg: 52, height_cm: 155, labMode: "scr", scr: 2.3, egfr: 30, ast: 24, alt: 20,
    total_bilirubin: 0.7, ...LIVER0, conditions: ["renal", "hypertension", "diabetes"], diagnosis: "", symptoms: "부종" },
  "건강한 성인": { age: 35, sex: "M", weight_kg: 72, height_cm: 176, labMode: "scr", scr: 0.9, egfr: 100, ast: 22, alt: 19,
    total_bilirubin: 0.6, ...LIVER0, conditions: [], diagnosis: "", symptoms: "두통" },
};
export const DEFAULT_PRESET = "건강한 성인";

export const hasLiverDisease = (p) => (p.conditions || []).some((c) => c === "hepatic" || c === "cirrhosis");

/** The patient fields the server takes (sent with /assess, /predict, /recommend and the general chat). */
export function patientPayload(patient) {
  const p = { age: patient.age, sex: patient.sex, weight_kg: patient.weight_kg, height_cm: patient.height_cm,
    ast: patient.ast, alt: patient.alt,
    total_bilirubin: patient.total_bilirubin, conditions: patient.conditions, diagnosis: patient.diagnosis || null,
    symptoms: patient.symptoms || null };
  if (patient.labMode === "scr") p.scr = patient.scr; else p.egfr = patient.egfr;
  if (hasLiverDisease(patient)) {  // Child-Pugh is graded only for a known liver disease (see PatientPanel)
    Object.assign(p, { albumin: patient.albumin, inr: patient.inr, ascites: patient.ascites,
      encephalopathy: patient.encephalopathy, child_pugh: patient.child_pugh || null });
  }
  return p;
}
