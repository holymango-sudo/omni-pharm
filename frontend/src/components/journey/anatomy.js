import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

/* Procedural anatomy for the body view: an X-ray style body, skeleton, organs and vessels.
   Units: the body stands at the origin, head top ~3.6, shins fade out below ~-3.6; it faces +z
   (the person's right side is on the screen's left). */

export const V = (x, y, z = 0) => new THREE.Vector3(x, y, z);

// ------------------------------------------------------------------ X-ray (fresnel) material
const VS = /* glsl */ `
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vW = w.xyz;
  vec4 mv = viewMatrix * w;
  vN = normalize(normalMatrix * normal);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;
const FS = /* glsl */ `
uniform vec3 uColor; uniform float uOpacity; uniform float uPower; uniform float uFill; uniform float uFade;
uniform float uScan; uniform float uLines; uniform float uPulse;
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  float ndv = abs(dot(normalize(vN), normalize(vV)));
  float rim = pow(1.0 - ndv, uPower);
  float lines = mix(1.0, 0.7 + 0.3 * sin(vW.y * 140.0), uLines);
  float band = exp(-pow((vW.y - uScan) * 10.0, 2.0));
  float fade = smoothstep(uFade, uFade + 1.3, vW.y);
  float k = (rim * 1.3 + uFill + uPulse * 0.45) * lines + band * 0.22 * uLines;
  gl_FragColor = vec4(uColor * k, clamp(k * uOpacity, 0.0, 1.0) * fade);
}`;

export function xray({ color = "#4fd8ff", opacity = 0.9, power = 2.4, fill = 0.03, fade = -99, lines = 1 } = {}) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(color) }, uOpacity: { value: opacity }, uPower: { value: power },
      uFill: { value: fill }, uFade: { value: fade }, uScan: { value: -10 }, uLines: { value: lines }, uPulse: { value: 0 },
    },
    vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  });
}

// ------------------------------------------------------------------ helpers
const lathe = (profile, seg = 40) => new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(Math.max(r, 0.0005), y)), seg);

/** a rounded limb between two points with a radius profile [[t, r], ...] (t = 0..1 along the limb) */
function limb(a, b, radii, seg = 22) {
  const len = a.distanceTo(b);
  const g = lathe(radii.map(([t, r]) => [r, t * len]), seg);
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), b.clone().sub(a).normalize()));
  g.translate(a.x, a.y, a.z);
  return g;
}

/** sphere deformed by f(unit vertex) -> new unit-space vertex, then scaled / rotated / placed */
function blob(f, scale, pos, rotZ = 0, detail = [36, 24]) {
  const g = new THREE.SphereGeometry(1, detail[0], detail[1]);
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    f(v);
    p.setXYZ(i, v.x * scale[0], v.y * scale[1], v.z * scale[2]);
  }
  if (rotZ) g.rotateZ(rotZ);
  g.translate(pos.x, pos.y, pos.z);
  g.computeVertexNormals();
  return g;
}

/** tube with a radius profile along the path */
function tube(points, radius, profile = () => 1, seg = 80, radial = 12) {
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal");
  const g = new THREE.TubeGeometry(curve, seg, radius, radial, false);
  const p = g.attributes.position, uv = g.attributes.uv;
  const c = new THREE.Vector3(), v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    const t = uv.getX(i);
    curve.getPointAt(Math.min(1, t), c);
    v.fromBufferAttribute(p, i).sub(c).multiplyScalar(profile(t)).add(c);
    p.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals();
  return g;
}

const merge = (gs) => mergeGeometries(gs.map((g) => (g.index ? g.toNonIndexed() : g)), false);

// ------------------------------------------------------------------ body + skeleton
// torso + neck + head silhouette (radius, height) - male: broad shoulders, straight waist; female: narrower shoulders,
// defined waist, wider hips (anthropometric proportions, schematic)
const TORSO = {
  M: [[0, -1.12], [0.42, -1.08], [0.64, -0.92], [0.73, -0.62], [0.7, -0.25], [0.61, 0.2], [0.64, 0.62], [0.75, 1.1], [0.82, 1.55],
    [0.86, 1.88], [0.7, 2.12], [0.32, 2.3], [0.17, 2.4], [0.16, 2.6], [0.24, 2.7], [0.36, 2.84], [0.41, 3.04], [0.39, 3.3],
    [0.28, 3.5], [0.0, 3.6]],
  F: [[0, -1.1], [0.46, -1.06], [0.7, -0.9], [0.8, -0.62], [0.74, -0.28], [0.54, 0.22], [0.56, 0.62], [0.66, 1.1], [0.72, 1.55],
    [0.75, 1.86], [0.6, 2.1], [0.28, 2.28], [0.14, 2.4], [0.135, 2.6], [0.22, 2.7], [0.34, 2.84], [0.39, 3.04], [0.37, 3.3],
    [0.27, 3.48], [0.0, 3.58]],
};
const LIMBS = {
  M: { sh: 0.84, el: 1.06, wr: 1.2, ua: [[0, 0.17], [0.15, 0.165], [0.7, 0.13], [1, 0.11]], fa: [[0, 0.11], [0.25, 0.12], [0.85, 0.08], [1, 0.07]],
    hip: 0.36, knee: 0.41, th: [[0, 0.31], [0.2, 0.29], [0.8, 0.2], [1, 0.17]], sk: [[0, 0.17], [0.25, 0.16], [0.85, 0.09], [1, 0.08]] },
  F: { sh: 0.74, el: 0.96, wr: 1.1, ua: [[0, 0.14], [0.15, 0.14], [0.7, 0.11], [1, 0.09]], fa: [[0, 0.09], [0.25, 0.1], [0.85, 0.065], [1, 0.06]],
    hip: 0.4, knee: 0.38, th: [[0, 0.34], [0.2, 0.31], [0.8, 0.2], [1, 0.16]], sk: [[0, 0.16], [0.3, 0.15], [0.85, 0.08], [1, 0.07]] },
};

export function bodyGeometry(sex = "M") {
  const k = sex === "F" ? "F" : "M";
  const torso = lathe(TORSO[k], 56);
  const p = torso.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const y = p.getY(i);
    const d = y < 2.25 ? 0.58 : y < 2.62 ? THREE.MathUtils.lerp(0.58, 1.0, (y - 2.25) / 0.37) : 1.12;
    p.setZ(i, p.getZ(i) * d);
  }
  torso.computeVertexNormals();
  const L = LIMBS[k];
  const parts = [torso];
  for (const s of [-1, 1]) {
    parts.push(limb(V(L.sh * s, 1.96), V(L.el * s, 0.78), L.ua));
    parts.push(limb(V(L.el * s, 0.78), V(L.wr * s, -0.42, 0.05), L.fa));
    parts.push(limb(V(L.wr * s, -0.42, 0.05), V((L.wr + 0.06) * s, -0.92, 0.08), [[0, 0.065], [0.3, 0.088], [0.75, 0.075], [1, 0.001]]));
    parts.push(limb(V(L.hip * s, -0.92), V(L.knee * s, -2.58), L.th));
    parts.push(limb(V(L.knee * s, -2.58), V((L.knee + 0.01) * s, -4.3), L.sk));
    if (k === "F") parts.push(blob((v) => { if (v.z < 0) v.z *= 0.3; if (v.y > 0.2) v.z *= 1 - 0.4 * (v.y - 0.2); }, [0.19, 0.18, 0.17],
      V(0.26 * s, 1.4, 0.39))); // breast
  }
  return merge(parts);
}

export function skeletonGeometry(sex = "M") {
  const wide = sex === "F" ? 1.16 : 1; // the female pelvis is wider and shallower
  const gs = [];
  for (let i = 0; i < 26; i++) { // vertebrae with the spine's S-curve
    const y = -0.85 + i * 0.13;
    const v = new THREE.CylinderGeometry(0.05 - i * 0.0008, 0.05 - i * 0.0008, 0.085, 10);
    v.translate(0, y, -0.24 + 0.05 * Math.sin(y * 1.15));
    gs.push(v);
  }
  for (let i = 0; i < 10; i++) { // ribs
    const y = 2.0 - i * 0.115;
    const w = 0.72 + 0.28 * Math.sin((Math.PI * (i + 1.5)) / 11.5);
    for (const s of [-1, 1]) {
      gs.push(tube([V(0.05 * s, y, -0.23), V(0.45 * s * w, y - 0.03, -0.2), V(0.68 * s * w, y - 0.12, 0.0),
        V(0.6 * s * w, y - 0.21, 0.22), V(0.2 * s, y - 0.28 - i * 0.012, 0.33)], 0.013, () => 1, 40, 5));
    }
  }
  gs.push(tube([V(0, 2.05, 0.34), V(0, 1.6, 0.37), V(0, 1.12, 0.35)], 0.028, () => 1, 20, 6)); // sternum
  for (const s of [-1, 1]) {
    gs.push(tube([V(0.05 * s, 2.2, 0.26), V(0.36 * s, 2.24, 0.18), V((sex === "F" ? 0.64 : 0.72) * s, 2.14, 0.02)], 0.022, () => 1, 20, 6)); // clavicle
    gs.push(tube([V(0.08 * s * wide, -0.95, 0.2), V(0.45 * s * wide, -0.75, 0.12), V(0.62 * s * wide, -0.42, -0.05),
      V(0.36 * s * wide, -0.3, -0.25), V(0.08 * s, -0.55, -0.26)], 0.035, () => 1, 40, 8)); // pelvis
    const hip = sex === "F" ? 0.41 : 0.37, knee = sex === "F" ? 0.38 : 0.41, sh = sex === "F" ? 0.76 : 0.86, el = sex === "F" ? 0.96 : 1.06;
    gs.push(limb(V(hip * s, -0.95), V(knee * s, -2.52), [[0, 0.06], [0.08, 0.04], [0.5, 0.032], [0.93, 0.045], [1, 0.03]], 10)); // femur
    gs.push(limb(V((knee + 0.005) * s, -2.62), V((knee + 0.01) * s, -4.2), [[0, 0.045], [0.1, 0.035], [0.9, 0.028], [1, 0.035]], 10)); // tibia
    gs.push(limb(V(sh * s, 1.98), V(el * s, 0.82), [[0, 0.045], [0.1, 0.03], [0.9, 0.028], [1, 0.04]], 10)); // humerus
  }
  return merge(gs);
}

// ------------------------------------------------------------------ organs (world positions baked in)
function lung(s) { // s = -1: the person's right lung (screen left)
  return blob((v) => {
    if (v.y > 0) { const k = 1 - 0.5 * v.y * v.y; v.x *= k; v.z *= k; }
    const medial = -s * v.x; // toward the heart
    if (medial > 0.35) v.x = -s * (0.35 + (medial - 0.35) * (s > 0 ? 0.15 : 0.3));
    if (v.y < -0.55) v.y = -0.55 + (v.y + 0.55) * 0.35;
  }, [s > 0 ? 0.25 : 0.28, 0.5, 0.22], V(0.43 * s, 1.66, 0.0));
}

function kidney(s) {
  return blob((v) => {
    const medial = -s * v.x;
    if (medial > 0.15 && Math.abs(v.y) < 0.55) v.x += s * 0.42 * (1 - Math.abs(v.y) / 0.55) * (medial - 0.15);
  }, [0.085, 0.15, 0.075], V(0.4 * s, s < 0 ? 0.31 : 0.37, -0.16), s * 0.15);
}

export const GUT_LOOPS = (() => { // small intestine packed in meandering rows
  const pts = [V(0.12, 0.47, 0.22)];
  const rows = [0.26, 0.1, -0.06, -0.22, -0.38, -0.52];
  rows.forEach((y, r) => {
    const dir = r % 2 === 0 ? 1 : -1;
    for (let k = 0; k <= 4; k++) {
      const x = dir * (-0.3 + (k * 0.6) / 4);
      pts.push(V(x, y + 0.04 * Math.sin(k * 2.1 + r), 0.24 + 0.06 * Math.sin(k * 1.7 + r * 0.8)));
    }
  });
  pts.push(V(-0.36, -0.55, 0.2));
  return pts;
})();

export const COLON = [V(-0.42, -0.52, 0.18), V(-0.47, -0.1, 0.2), V(-0.45, 0.32, 0.2), V(-0.36, 0.44, 0.24), V(0, 0.4, 0.3),
  V(0.38, 0.44, 0.24), V(0.5, 0.32, 0.16), V(0.48, -0.1, 0.16), V(0.45, -0.5, 0.18), V(0.25, -0.72, 0.2), V(0.02, -0.86, 0.06)];
export const STOMACH = [V(0.16, 1.03, 0.06), V(0.3, 1.0, 0.13), V(0.44, 0.86, 0.18), V(0.43, 0.67, 0.21), V(0.3, 0.56, 0.23),
  V(0.15, 0.55, 0.23)];

/** uterus + ovaries (female; enlarged when pregnant) or prostate + seminal vesicles (male) */
function reproductive(sex, pregnant) {
  if (sex === "F") {
    if (pregnant) return blob((v) => { if (v.y < -0.5) v.y = -0.5 + (v.y + 0.5) * 0.5; }, [0.3, 0.38, 0.3], V(0, -0.45, 0.26));
    return merge([
      blob((v) => { const t = (v.y + 1) / 2; v.x *= 0.55 + 0.45 * t; v.z *= 0.6 + 0.4 * t; }, [0.11, 0.13, 0.08], V(0, -0.8, 0.12)),
      ...[-1, 1].map((s) => blob(() => {}, [0.045, 0.03, 0.03], V(0.24 * s, -0.75, 0.1))),
      ...[-1, 1].map((s) => tube([V(0.08 * s, -0.7, 0.12), V(0.17 * s, -0.66, 0.12), V(0.24 * s, -0.72, 0.1)], 0.01, () => 1, 16, 5)),
    ]);
  }
  return merge([
    blob(() => {}, [0.075, 0.06, 0.065], V(0, -0.97, 0.1)),
    ...[-1, 1].map((s) => blob(() => {}, [0.06, 0.025, 0.025], V(0.08 * s, -0.86, 0.02), 0.5 * s)),
  ]);
}

export function organGeometries(sex = "M", pregnant = false) {
  const brain = merge([
    ...[-1, 1].map((s) => blob((v) => {
      const d = 1 + 0.05 * Math.sin(15 * v.x + 6 * v.y) * Math.sin(13 * v.z + 4 * v.y) + 0.035 * Math.sin(24 * v.y + 11 * v.z);
      v.multiplyScalar(d);
      if (-s * v.x > 0.75) v.x = -s * 0.75; // flat medial wall between the hemispheres
    }, [0.16, 0.19, 0.29], V(0.085 * s, 3.13, 0.0), 0, [48, 32])),
    blob(() => {}, [0.13, 0.08, 0.11], V(0, 2.92, -0.2)),
    limb(V(0, 2.95, -0.1), V(0, 2.62, -0.12), [[0, 0.05], [1, 0.04]], 10),
  ]);
  const heart = blob((v) => {
    if (v.y < 0) { v.y *= 1.3; v.x += -0.4 * v.y; }
    v.z *= 0.9;
  }, [0.15, 0.16, 0.13], V(0.12, 1.42, 0.18), -0.35);
  const liver = merge([
    blob((v) => {
      const t = (v.x + 1) / 2;
      v.y *= 1 - 0.62 * t;
      v.z *= 1 - 0.3 * t;
      if (v.y < 0) v.y *= 0.55;
    }, [0.47, 0.26, 0.27], V(-0.3, 0.8, 0.12), -0.12),
    blob(() => {}, [0.035, 0.07, 0.035], V(-0.2, 0.6, 0.3), 0.4), // gallbladder
  ]);
  return {
    brain, heart, liver,
    lung: merge([lung(-1), lung(1)]),
    eye: merge([-1, 1].map((s) => blob(() => {}, [0.045, 0.045, 0.045], V(0.14 * s, 3.0, 0.36)))),
    thyroid: merge([-1, 1].map((s) => blob(() => {}, [0.045, 0.075, 0.035], V(0.055 * s, 2.43, 0.17))).concat(
      [blob(() => {}, [0.05, 0.02, 0.025], V(0, 2.41, 0.19))])),
    stomach: tube(STOMACH, 0.09, (t) => 0.55 + 0.95 * Math.sin(Math.PI * Math.min(1, t * 1.15)) ** 0.7, 60, 16),
    pancreas: tube([V(0.0, 0.5, 0.08), V(0.18, 0.55, 0.06), V(0.38, 0.62, 0.03)], 0.05, (t) => 1 - 0.5 * t, 30, 10),
    intestine: merge([
      tube(GUT_LOOPS, 0.045, () => 1, 220, 8),
      tube(COLON, 0.06, (t) => 1 + 0.18 * Math.sin(t * 90), 160, 10),
    ]),
    kidney: merge([kidney(-1), kidney(1),
      tube([V(-0.36, 0.22, -0.12), V(-0.2, -0.3, -0.05), V(-0.05, -0.74, 0.12)], 0.012, () => 1, 20, 5),
      tube([V(0.36, 0.28, -0.12), V(0.2, -0.3, -0.05), V(0.05, -0.74, 0.12)], 0.012, () => 1, 20, 5)]),
    bladder: blob((v) => { if (v.y > 0.3) v.y = 0.3 + (v.y - 0.3) * 0.6; }, [0.12, 0.1, 0.1], V(0, -0.8, 0.18)),
    reproductive: reproductive(sex, pregnant),
    muscle: merge([-1].map((s) => blob((v) => { v.z *= 0.9; }, [0.2, 0.66, 0.19], V(0.4 * s, -1.75, 0.08)))),
    bone: limb(V(0.38, -0.95), V(0.41, -2.52), [[0, 0.07], [0.08, 0.05], [0.5, 0.045], [0.93, 0.06], [1, 0.04]], 12),
    joint: merge([new THREE.TorusGeometry(0.16, 0.03, 10, 40).rotateX(Math.PI / 2).translate(0.415, -2.6, 0.02),
      blob(() => {}, [0.07, 0.08, 0.04], V(0.415, -2.58, 0.17))]),
    skin: new THREE.CylinderGeometry(0.135, 0.13, 0.5, 24, 1, true, -0.6, 3.6).rotateZ(-0.1).translate(-1.15, 0.2, 0.06),
    infection: merge([[0, 0, 0], [0.17, 0.11, -0.05], [-0.12, 0.16, 0.06]].flatMap(([x, y, z]) => {
      const c = V(-0.8 + x, 2.02 + y, 0.42 + z);
      const spikes = [];
      const ico = new THREE.IcosahedronGeometry(0.055, 1);
      const p = ico.attributes.position;
      for (let i = 0; i < p.count; i += 9) {
        const d = V(p.getX(i), p.getY(i), p.getZ(i)).normalize();
        const sp = new THREE.CylinderGeometry(0.004, 0.004, 0.035, 4);
        sp.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), d));
        sp.translate(c.x + d.x * 0.07, c.y + d.y * 0.07, c.z + d.z * 0.07);
        spikes.push(sp);
      }
      return [ico.translate(c.x, c.y, c.z), ...spikes];
    })),
  };
}

// ------------------------------------------------------------------ vessels
export const ARTERIES = [
  [V(0.1, 1.5, 0.12), V(0.12, 1.92, 0.04), V(0.2, 2.0, -0.08), V(0.1, 1.7, -0.2), V(0.04, 1.1, -0.2), V(0.0, 0.3, -0.16),
    V(0.0, -0.5, -0.12)], // aorta
  [V(0.0, -0.5, -0.12), V(0.22, -0.8, -0.04), V(0.36, -1.1, 0.02), V(0.4, -2.0, 0.06), V(0.42, -2.6, -0.02), V(0.42, -3.9, 0.04)],
  [V(0.0, -0.5, -0.12), V(-0.22, -0.8, -0.04), V(-0.36, -1.1, 0.02), V(-0.4, -2.0, 0.06), V(-0.42, -2.6, -0.02), V(-0.42, -3.9, 0.04)],
  [V(0.14, 1.98, 0.0), V(0.08, 2.4, 0.06), V(0.1, 2.75, 0.04), V(0.07, 3.05, 0.0)], // carotids
  [V(0.1, 1.98, 0.0), V(-0.08, 2.4, 0.06), V(-0.1, 2.75, 0.04), V(-0.07, 3.05, 0.0)],
  [V(0.18, 2.0, -0.04), V(0.6, 2.08, 0.0), V(0.95, 1.6, 0.04), V(1.12, 0.6, 0.08), V(1.22, -0.5, 0.08)], // arms
  [V(0.06, 2.0, -0.02), V(-0.6, 2.08, 0.0), V(-0.95, 1.6, 0.04), V(-1.12, 0.6, 0.08), V(-1.22, -0.5, 0.08)],
  [V(0.0, 0.38, -0.16), V(0.22, 0.37, -0.15), V(0.36, 0.37, -0.16)], // renal
  [V(0.0, 0.38, -0.16), V(-0.22, 0.33, -0.15), V(-0.36, 0.31, -0.16)],
  [V(0.01, 0.55, -0.15), V(0.05, 0.3, 0.05), V(0.0, 0.0, 0.2)], // mesenteric
];
export const VEINS = [
  [V(-0.08, -0.5, -0.08), V(-0.1, 0.3, -0.1), V(-0.12, 0.8, -0.05), V(-0.04, 1.25, 0.06), V(0.06, 1.42, 0.14)], // IVC
  [V(-0.1, 2.05, 0.02), V(-0.08, 1.7, 0.08), V(0.04, 1.48, 0.14)], // SVC
  [V(0.0, 0.0, 0.2), V(-0.08, 0.3, 0.14), V(-0.2, 0.62, 0.1)], // portal
  [V(-0.08, -0.5, -0.08), V(0.36, -1.1, 0.1), V(0.4, -2.0, 0.14), V(0.42, -3.9, 0.1)],
  [V(-0.08, -0.5, -0.08), V(-0.36, -1.1, 0.1), V(-0.4, -2.0, 0.14), V(-0.42, -3.9, 0.1)],
];

export function vesselGeometry(list, radius) {
  return merge(list.map((pts, i) => tube(pts, i === 0 ? radius * 1.6 : radius, () => 1, 60, 6)));
}
