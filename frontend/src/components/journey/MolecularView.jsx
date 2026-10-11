import React, { useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import Bloom from "./Bloom.jsx";
import { xray } from "./anatomy.js";

/* Molecular close-up next to the body: the real 3D structure of each drug (heavy atoms, from the molecule data the
   app already loaded) docking into the protein it acts on - a CYP enzyme with its heme, a 7-helix receptor, an ion
   channel, a transporter, a bacterium - or crossing the gut wall / binding metal ions. Schematic, not to scale. */

const now = () => performance.now() / 1000;
const V = (x, y, z = 0) => new THREE.Vector3(x, y, z);
const ELEM = { C: "#9fb8c8", N: "#4f7bff", O: "#ff4d6d", S: "#ffd23f", P: "#ff9f1c", F: "#9dff6a", Cl: "#3dff8f",
  Br: "#ff7a3d", I: "#b46bff", Fe: "#ff8c42", Na: "#c084fc", K: "#c084fc", Li: "#f472b6" };
const easeOut = (t) => 1 - (1 - Math.min(1, Math.max(0, t))) ** 3;

// ------------------------------------------------------------------ molecules
function pseudoAtoms(seed) { // biologics / foods have no small-molecule structure: a neutral stand-in
  let s = seed;
  const r = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  const atoms = [["C", 0, 0, 0]];
  const bonds = [];
  for (let i = 1; i < 9; i++) {
    const j = Math.floor(r() * i);
    const [, x, y, z] = atoms[j];
    atoms.push([r() < 0.25 ? "O" : r() < 0.2 ? "N" : "C", x + (r() - 0.5) * 2.6, y + (r() - 0.5) * 2.6, z + (r() - 0.5) * 2.6]);
    bonds.push([j, i, 1]);
  }
  return { atoms, bonds };
}

function prepMol(mol, size) {
  const src = mol?.atoms?.length ? mol : pseudoAtoms((mol?.key || "x").length * 97 + 13);
  const keep = src.atoms.map((a, i) => [a, i]).filter(([a]) => a[0] !== "H");
  const idx = new Map(keep.map(([, i], k) => [i, k]));
  const pts = keep.map(([a]) => V(a[1], a[2], a[3]));
  const c = pts.reduce((acc, p) => acc.add(p), V(0, 0, 0)).multiplyScalar(1 / Math.max(1, pts.length));
  pts.forEach((p) => p.sub(c));
  const R = Math.max(0.5, ...pts.map((p) => p.length()));
  pts.forEach((p) => p.multiplyScalar(size / R));
  const bonds = (src.bonds || []).filter(([i, j]) => idx.has(i) && idx.has(j)).map(([i, j, o]) => [idx.get(i), idx.get(j), o]);
  return { el: keep.map(([a]) => a[0]), pts, bonds, oxy: pts.filter((_, k) => keep[k][0][0] === "O") };
}

function Molecule({ data, tint, groupRef, atomR = 0.085, label }) {
  const atoms = useRef();
  const sticks = useRef();
  useLayoutEffect(() => {
    if (!atoms.current || !sticks.current) return;
    const m = new THREE.Matrix4(), c = new THREE.Color(), q = new THREE.Quaternion();
    data.pts.forEach((p, i) => {
      m.compose(p, q, V(atomR, atomR, atomR));
      atoms.current.setMatrixAt(i, m);
      atoms.current.setColorAt(i, c.set(ELEM[data.el[i]] || "#e879f9"));
    });
    atoms.current.instanceMatrix.needsUpdate = true;
    if (atoms.current.instanceColor) atoms.current.instanceColor.needsUpdate = true;
    data.bonds.forEach(([i, j], k) => {
      const a = data.pts[i], b = data.pts[j];
      const d = b.clone().sub(a);
      m.compose(a.clone().add(b).multiplyScalar(0.5), new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), d.clone().normalize()),
        V(1, d.length(), 1));
      sticks.current.setMatrixAt(k, m);
    });
    sticks.current.instanceMatrix.needsUpdate = true;
  }, [data, atomR]);
  return (
    <group ref={groupRef}>
      <instancedMesh ref={atoms} args={[null, null, Math.max(1, data.pts.length)]}>
        <sphereGeometry args={[1, 18, 14]} />
        <meshStandardMaterial roughness={0.3} metalness={0.1} emissive={tint} emissiveIntensity={0.1} />
      </instancedMesh>
      <instancedMesh ref={sticks} args={[null, null, Math.max(1, data.bonds.length)]}>
        <cylinderGeometry args={[0.03, 0.03, 1, 8]} />
        <meshStandardMaterial color="#8fa6b4" roughness={0.45} metalness={0.3} emissive={tint} emissiveIntensity={0.12} />
      </instancedMesh>
      {label && (
        <Html position={[0, -0.78, 0]} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
          <div className="mv-tag" style={{ borderColor: tint, color: tint }}>{label}</div>
        </Html>
      )}
    </group>
  );
}

// ------------------------------------------------------------------ proteins
function globuleGeometry(radius, pocket = V(0, 0.05, 1).normalize(), depth = 0.5) {
  const g = new THREE.IcosahedronGeometry(1, 6);
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i).normalize();
    let d = 1 + 0.09 * Math.sin(5 * v.x + 2) * Math.sin(6 * v.y + 1) + 0.06 * Math.sin(9 * v.z + 4 * v.x)
      + 0.035 * Math.sin(17 * v.y + 13 * v.x);
    d -= depth * Math.exp(-v.distanceToSquared(pocket) / 0.09); // the active-site pocket
    p.setXYZ(i, v.x * d * radius, v.y * d * radius, v.z * d * radius);
  }
  g.computeVertexNormals();
  return g;
}

function Protein({ geometry, color = "#2b7a99", glow, pulse = 0, opacity = 0.92 }) {
  const rim = useMemo(() => xray({ color: glow || "#7fe9ff", power: 2.0, fill: 0.02, lines: 0 }), [glow]);
  useFrame(() => { rim.uniforms.uPulse.value = pulse ? (0.5 + 0.5 * Math.sin(now() * 6)) * pulse : 0; });
  return (
    <group>
      <mesh geometry={geometry}>
        <meshStandardMaterial color={color} roughness={0.55} metalness={0.1} transparent opacity={opacity} />
      </mesh>
      <mesh geometry={geometry} scale={1.01} material={rim} />
    </group>
  );
}

function Heme({ position }) {
  return (
    <group position={position}>
      <mesh><torusGeometry args={[0.2, 0.028, 10, 40]} /><meshStandardMaterial color="#d97706" emissive="#b45309" emissiveIntensity={0.6} /></mesh>
      {[0, 1, 2, 3].map((k) => (
        <mesh key={k} position={[Math.cos((k * Math.PI) / 2 + 0.78) * 0.2, Math.sin((k * Math.PI) / 2 + 0.78) * 0.2, 0]}>
          <sphereGeometry args={[0.055, 12, 10]} /><meshStandardMaterial color="#f59e0b" emissive="#f59e0b" emissiveIntensity={0.4} />
        </mesh>
      ))}
      <mesh><sphereGeometry args={[0.075, 16, 12]} /><meshStandardMaterial color="#ff8c42" emissive="#ff6a00" emissiveIntensity={1.4} /></mesh>
    </group>
  );
}

function Membrane({ width = 4.4, depth = 1.6, y = 0, gap = 0.55, tint = "#5eead4" }) {
  const heads = useRef();
  const n = useMemo(() => {
    const out = [];
    for (let x = -width / 2; x <= width / 2; x += 0.16) {
      for (let z = -depth / 2; z <= depth / 2; z += 0.16) { out.push([x, y + gap, z], [x, y - gap, z]); }
    }
    return out;
  }, [width, depth, y, gap]);
  useLayoutEffect(() => {
    if (!heads.current) return;
    const m = new THREE.Matrix4();
    n.forEach(([x, yy, z], i) => { m.makeTranslation(x + (Math.random() - 0.5) * 0.03, yy, z); heads.current.setMatrixAt(i, m); });
    heads.current.instanceMatrix.needsUpdate = true;
  }, [n]);
  return (
    <group>
      <instancedMesh ref={heads} args={[null, null, n.length]}>
        <sphereGeometry args={[0.055, 8, 6]} />
        <meshStandardMaterial color={tint} emissive={tint} emissiveIntensity={0.25} roughness={0.5} />
      </instancedMesh>
      <mesh position={[0, y, 0]}>
        <boxGeometry args={[width, gap * 1.6, depth]} />
        <meshStandardMaterial color="#0e3a4a" transparent opacity={0.22} />
      </mesh>
    </group>
  );
}

/** 7 transmembrane helices around a central ligand pocket (GPCR) / 5 subunits around a pore (channel) /
    2 x 6 helices (transporter) */
function Bundle({ kind, color, active, block }) {
  const g = useRef();
  const spec = kind === "channel" ? { n: 5, r: 0.36, rad: 0.17, h: 1.5 }
    : kind === "transporter" ? { n: 10, r: 0.42, rad: 0.1, h: 1.45 } : { n: 7, r: 0.38, rad: 0.12, h: 1.55 };
  const mats = useMemo(() => Array.from({ length: spec.n }, (_, k) => new THREE.MeshStandardMaterial({
    color: new THREE.Color(color).offsetHSL(k * 0.025 - 0.08, 0, 0), roughness: 0.4, metalness: 0.15,
    emissive: color, emissiveIntensity: 0.15 })), [color, spec.n]);
  useFrame(() => {
    const t = now();
    mats.forEach((m, k) => { m.emissiveIntensity = active ? 0.35 + 0.35 * Math.sin(t * 5 + k) * (block ? 0.2 : 1) : 0.15; });
    if (g.current) g.current.rotation.y = Math.sin(t * 0.4) * 0.25;
  });
  return (
    <group ref={g}>
      {mats.map((m, k) => {
        const a = (k / spec.n) * Math.PI * 2;
        const rr = kind === "transporter" ? spec.r * (k % 2 ? 0.75 : 1.05) : spec.r;
        return (
          <mesh key={k} material={m} position={[Math.cos(a) * rr, 0, Math.sin(a) * rr]} rotation={[Math.sin(a) * 0.22, 0, Math.cos(a) * 0.22]}>
            <cylinderGeometry args={[spec.rad, spec.rad, spec.h, 14]} />
          </mesh>
        );
      })}
    </group>
  );
}

function Ions({ flowing, color = "#fde047", top = 1.4, bottom = -1.4, n = 14 }) {
  const ref = useRef();
  const seeds = useMemo(() => Array.from({ length: n }, () => [Math.random(), (Math.random() - 0.5) * 0.12, (Math.random() - 0.5) * 0.12]), [n]);
  useFrame(() => {
    if (!ref.current) return;
    const t = now();
    ref.current.children.forEach((c, i) => {
      const [s, x, z] = seeds[i];
      if (flowing) {
        const u = (s + t * 0.35) % 1;
        c.position.set(x, top - u * (top - bottom), z);
      } else {
        c.position.set(x * 6 + Math.sin(t + i) * 0.05, top - 0.15 + Math.sin(t * 2 + i) * 0.05, z * 6);
      }
    });
  });
  return (
    <group ref={ref}>
      {seeds.map((_, i) => <mesh key={i}><sphereGeometry args={[0.045, 10, 8]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.2} /></mesh>)}
    </group>
  );
}

/** virion: capsid with spike proteins (for antivirals; the drug blocks a viral enzyme inside) */
function Virion() {
  const g = useRef();
  const spikes = useMemo(() => {
    const ico = new THREE.IcosahedronGeometry(1, 2);
    const p = ico.attributes.position;
    const out = [];
    const seen = new Set();
    for (let i = 0; i < p.count; i++) {
      const d = V(p.getX(i), p.getY(i), p.getZ(i)).normalize();
      const k = d.toArray().map((x) => x.toFixed(2)).join();
      if (!seen.has(k)) { seen.add(k); out.push(d); }
    }
    return out;
  }, []);
  useFrame(() => { if (g.current) g.current.rotation.y = now() * 0.25; });
  return (
    <group ref={g}>
      <mesh><icosahedronGeometry args={[0.72, 3]} /><meshStandardMaterial color="#4c1d95" emissive="#7c3aed" emissiveIntensity={0.25} transparent opacity={0.5} roughness={0.5} /></mesh>
      {spikes.map((d, i) => (
        <group key={i} position={d.clone().multiplyScalar(0.8)} quaternion={new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), d)}>
          <mesh><cylinderGeometry args={[0.015, 0.015, 0.16, 5]} /><meshStandardMaterial color="#c4b5fd" /></mesh>
          <mesh position={[0, 0.1, 0]}><sphereGeometry args={[0.04, 8, 6]} /><meshStandardMaterial color="#ddd6fe" emissive="#a78bfa" emissiveIntensity={0.4} /></mesh>
        </group>
      ))}
      <mesh><icosahedronGeometry args={[0.32, 2]} /><meshStandardMaterial color="#f59e0b" emissive="#f59e0b" emissiveIntensity={0.25} transparent opacity={0.6} /></mesh>
    </group>
  );
}

function Bacterium({ dim }) {
  const m = useRef();
  useFrame(() => { if (m.current) m.current.material.emissiveIntensity = dim ? 0.05 + 0.05 * Math.sin(now() * 3) : 0.25; });
  return (
    <group rotation={[0, 0, 0.3]}>
      <mesh ref={m}>
        <capsuleGeometry args={[0.55, 1.5, 12, 24]} />
        <meshStandardMaterial color="#3f6212" emissive="#84cc16" emissiveIntensity={0.25} transparent opacity={0.55} roughness={0.6} />
      </mesh>
      {[-0.5, -0.15, 0.2, 0.55].map((y, k) => (
        <mesh key={k} position={[((k % 2) - 0.5) * 0.4, y, 0.1]}>
          <sphereGeometry args={[0.13, 14, 10]} /><meshStandardMaterial color="#a3e635" emissive="#a3e635" emissiveIntensity={0.3} />
        </mesh>
      ))}
    </group>
  );
}

// ------------------------------------------------------------------ scenes
function Docking({ mol, color, from, to, t0 = 0, dur = 2.4, spin = 2.2, after, label }) {
  const g = useRef();
  const start = useMemo(now, []);
  useFrame(() => {
    if (!g.current) return;
    const t = now() - start - t0;
    if (t < 0) { g.current.visible = false; return; }
    g.current.visible = true;
    const u = easeOut(t / dur);
    g.current.position.lerpVectors(from, to, u);
    g.current.rotation.y += (1 - u) * 0.04 * spin + 0.003;
    g.current.rotation.x = (1 - u) * 1.2;
    after?.(g.current, t - dur);
  });
  return <Molecule data={mol} tint={color} groupRef={g} label={label} />;
}

function Tag3({ position, children, color = "#7fe9ff" }) {
  return (
    <Html position={position} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
      <div className="mv-tag" style={{ borderColor: color, color }}>{children}</div>
    </Html>
  );
}

function EnzymeScene({ a, b, heme, inhibit, induce, name, color = "#1f5f7a" }) {
  const pocket = V(0, 0.08, 1.12);
  const geo = useMemo(() => globuleGeometry(1.25), []);
  const ring = useRef();
  const start = useMemo(now, []);
  useFrame(() => {
    if (!ring.current) return;
    const t = now() - start;
    ring.current.rotation.z += induce ? 0.08 : 0.01;
    ring.current.material.opacity = t > 2.4 ? 0.55 + 0.4 * Math.sin(t * 6) : 0.15;
  });
  const bounce = (grp, t) => { // the blocked substrate is pushed back from the occupied pocket, again and again
    const cyc = ((t % 3) + 3) % 3;
    const u = cyc < 1.4 ? easeOut(cyc / 1.4) : 1 - easeOut((cyc - 1.4) / 1.2);
    grp.position.lerpVectors(V(-2.9, -1.2, 1.4), V(-0.62, -0.05, 1.5), u);
    grp.scale.setScalar(1);
  };
  const metabolize = (grp, t) => { // a free pocket turns the substrate over: it shrinks and leaves as a metabolite
    if (t < 0.8) return;
    const u = Math.min(1, (t - 0.8) / 1.6);
    grp.scale.setScalar(1 - 0.55 * u);
    grp.position.lerpVectors(V(0, 0.08, 1.35), V(2.4, -1.4, 1.2), easeOut(u));
  };
  return (
    <group>
      <Protein geometry={geo} color={color} glow={inhibit ? "#ff6b85" : "#7fe9ff"} pulse={inhibit ? 0.5 : 0} opacity={0.8} />
      {heme && <Heme position={[0, 0.08, 0.98]} />}
      {heme && <Tag3 position={[0.42, 0.42, 1.0]} color="#ffb366">Heme · Fe</Tag3>}
      {name && <Tag3 position={[0, 1.55, 0]}>{name}</Tag3>}
      <mesh ref={ring} position={[0, 0.08, 1.05]}>
        <torusGeometry args={[0.42, 0.012, 8, 64]} />
        <meshBasicMaterial color={inhibit ? "#ff2d55" : "#00e5ff"} transparent opacity={0.2} />
      </mesh>
      {a && <Docking mol={a.data} color={a.color} from={V(2.9, 1.5, 1.6)} to={pocket.clone().add(V(0, 0, 0.25))}
        after={!inhibit && !b ? metabolize : undefined} label={`${a.name} · ${inhibit ? "억제제" : induce ? "유도제" : "기질"}`} />}
      {b && <Docking mol={b.data} color={b.color} from={V(-2.9, -1.2, 1.4)} to={V(-0.62, -0.05, 1.5)} t0={2.6} dur={1.4}
        after={inhibit ? bounce : metabolize} label={`${b.name} · 기질${inhibit ? " ✕" : ""}`} />}
    </group>
  );
}

function ReceptorScene({ items }) {
  const xs = items.length > 1 ? [-1.05, 1.05] : [0];
  return (
    <group position={[0, -0.1, 0]}>
      {items.some((it) => it.kind !== "pathogen") && <Membrane />}
      {items.map((it, k) => {
        const x = xs[k];
        const block = /차단|막|억제/.test(it.action || it.label || "");
        let protein;
        if (it.kind === "pathogen") protein = /바이러스|virus|HIV|간염/i.test(it.label) ? <Virion /> : <Bacterium dim />;
        else if (it.kind === "gpcr" || it.kind === "channel" || it.kind === "transporter") {
          protein = <Bundle kind={it.kind} color={it.kind === "gpcr" ? "#6d5dfc" : it.kind === "channel" ? "#0ea5a4" : "#3b82f6"} active block={block} />;
        } else protein = <Protein geometry={globuleGeometry(0.62, V(0, 1, 0.3).normalize(), 0.45)} color="#2b7a99" />;
        return (
          <group key={k} position={[x, 0, 0]}>
            {protein}
            {it.kind === "channel" && <Ions flowing={!block} />}
            {it.kind === "gpcr" && !block && <GProtein />}
            <Tag3 position={[0, -1.35, 0.3]}>{it.label.length > 18 ? `${it.label.slice(0, 17)}…` : it.label}</Tag3>
            <Docking mol={it.data} color={it.color} from={V(x > 0 ? 1.4 : -1.4, 2.6, 1.2)} to={V(0, it.kind === "pathogen" ? 0.2 : 0.62, 0.15)}
              t0={k * 0.6} dur={2.2} label={it.name} />
          </group>
        );
      })}
    </group>
  );
}

function GProtein() {
  const g = useRef();
  useFrame(() => { if (g.current) g.current.scale.setScalar(1 + 0.12 * Math.max(0, Math.sin(now() * 4))); });
  return (
    <group ref={g} position={[0, -1.05, 0.1]}>
      {[[-0.18, 0, 0, "#f472b6"], [0.12, 0.05, 0.08, "#fb923c"], [0.05, -0.18, -0.05, "#facc15"]].map(([x, y, z, c], k) => (
        <mesh key={k} position={[x, y, z]}><sphereGeometry args={[0.16, 16, 12]} /><meshStandardMaterial color={c} emissive={c} emissiveIntensity={0.4} /></mesh>
      ))}
    </group>
  );
}

function MembraneScene({ items, gutEnzyme, pump }) {
  const groups = useRef([]);
  const seeds = useMemo(() => items.flatMap((it, k) => Array.from({ length: 3 }, (_, i) => ({ it, s: (i / 3 + k * 0.17) % 1, x: (i - 1) * 0.9 + k * 0.3 }))), [items]);
  useFrame(() => {
    const t = now();
    seeds.forEach((sd, i) => {
      const g = groups.current[i];
      if (!g) return;
      const u = (sd.s + t * 0.16) % 1;
      g.position.set(sd.x + Math.sin(t + i) * 0.1, 2.2 - u * 4.4, 0.3);
      g.rotation.y += 0.01;
    });
  });
  return (
    <group>
      <Membrane width={4.8} depth={1.8} />
      <Tag3 position={[-1.7, 1.25, 0.6]}>장 내강</Tag3>
      <Tag3 position={[-1.7, -1.25, 0.6]} color="#ff8fa3">혈액 (문맥)</Tag3>
      {gutEnzyme && (
        <group position={[-1.6, 0, 0.2]}>
          <Protein geometry={globuleGeometry(0.42)} color="#475569" glow="#ff6b85" pulse={0.6} opacity={0.75} />
        </group>
      )}
      {pump && <group position={[1.6, 0, 0]}><Bundle kind="transporter" color="#64748b" active block /></group>}
      {seeds.map((sd, i) => (
        <Molecule key={i} data={sd.it.data} tint={sd.it.color} groupRef={(el) => { groups.current[i] = el; }} />
      ))}
    </group>
  );
}

function ChelationScene({ drug, color, name, ionLabelColor = "#d9f99d" }) {
  const ions = useRef([]);
  const grp = useRef();
  const start = useMemo(now, []);
  const sites = useMemo(() => {
    const o = drug.oxy.length ? drug.oxy : drug.pts;
    return [0, 1, 2].map((k) => o[Math.min(o.length - 1, k * Math.max(1, Math.floor(o.length / 3)))].clone().multiplyScalar(1.25));
  }, [drug]);
  useFrame(() => {
    const t = now() - start;
    ions.current.forEach((m, k) => {
      if (!m) return;
      const from = V(Math.cos(k * 2.1) * 2.6, Math.sin(k * 2.1) * 1.8, 1.0);
      m.position.lerpVectors(from, sites[k], easeOut((t - k * 0.4) / 1.8));
    });
    if (grp.current) {
      const s = Math.max(0, t - 2.8);
      grp.current.position.y = -Math.min(1.2, s * 0.35); // the insoluble complex sinks: it is not absorbed
      grp.current.rotation.y = t * 0.3;
    }
  });
  return (
    <group ref={grp}>
      <Molecule data={drug} tint={color} label={name} />
      <Tag3 position={[1.3, 1.2, 0.5]} color={ionLabelColor}>Ca²⁺ · Fe²⁺ · Mg²⁺</Tag3>
      {sites.map((_, k) => (
        <mesh key={k} ref={(el) => { ions.current[k] = el; }}>
          <sphereGeometry args={[0.17, 20, 14]} />
          <meshStandardMaterial color={ionLabelColor} emissive={ionLabelColor} emissiveIntensity={0.55} roughness={0.3} />
        </mesh>
      ))}
    </group>
  );
}

function DissolveScene({ items }) {
  const tab = useRef();
  const mols = useRef([]);
  const start = useMemo(now, []);
  const parts = useMemo(() => items.flatMap((it) => [0, 1, 2].map((k) => ({ it, dir: V(Math.cos(k * 2.1 + it.color.length), Math.sin(k * 1.7) * 0.8, Math.sin(k * 2.3)).normalize() }))), [items]);
  useFrame(() => {
    const t = now() - start;
    if (tab.current) {
      tab.current.rotation.y = t * 0.8;
      const s = Math.max(0, 1 - Math.max(0, t - 1.2) / 1.2);
      tab.current.scale.setScalar(Math.max(0.001, s));
    }
    parts.forEach((p, i) => {
      const g = mols.current[i];
      if (!g) return;
      const u = easeOut((t - 1.3) / 2.2);
      g.visible = t > 1.3;
      g.position.copy(p.dir).multiplyScalar(u * 1.9);
      g.rotation.y += 0.01;
    });
  });
  return (
    <group>
      <mesh ref={tab} rotation={[0, 0, Math.PI / 2]}>
        <capsuleGeometry args={[0.38, 0.9, 10, 24]} />
        <meshStandardMaterial color="#e2e8f0" emissive="#94a3b8" emissiveIntensity={0.3} roughness={0.3} />
      </mesh>
      {parts.map((p, i) => <Molecule key={i} data={p.it.data} tint={p.it.color} groupRef={(el) => { mols.current[i] = el; }} />)}
    </group>
  );
}

// ------------------------------------------------------------------ picking what to show
/** -> {scene, caption, sub} for the current stage (or null when the side panel shows a chart instead) */
export function molecularPlan(stage, pathway, molByKey, colorOf) {
  const nameOf = (k) => pathway.drugs.find((d) => d.key === k)?.name || k;
  const mk = (k, size = 0.62) => ({ key: k, name: nameOf(k), color: colorOf(k), data: prepMol(molByKey[k], size) });
  const drugs = pathway.drugs;
  if (stage.id === "intake") {
    const ch = (stage.items || []).find((i) => i.kind === "chelation");
    if (ch) {
      return { scene: <ChelationScene drug={prepMol(molByKey[ch.drugs[1]], 0.75)} color={colorOf(ch.drugs[1])} name={nameOf(ch.drugs[1])} />,
        caption: `킬레이트 형성 · ${nameOf(ch.drugs[1])} + 금속 이온(Ca²⁺·Fe²⁺·Mg²⁺)`, sub: "불용성 복합체 → 장에서 흡수되지 못함", sev: "mid" };
    }
    const oral = drugs.filter((d) => d.route === "oral" && d.kind !== "food").slice(0, 3);
    return { scene: <DissolveScene items={oral.map((d) => mk(d.key, 0.45))} />, caption: "붕해 · 용해",
      sub: "정제가 녹아 약 분자가 위액으로 풀려나옵니다", sev: "normal" };
  }
  if (stage.id === "absorb") {
    const it = (stage.items || [])[0];
    const items = drugs.filter((d) => d.route === "oral" && d.kind !== "food").slice(0, 3).map((d) => mk(d.key, 0.4));
    return { scene: <MembraneScene items={items} gutEnzyme={it?.kind === "gut_cyp"} pump={it?.kind === "pgp"} />,
      caption: it?.kind === "gut_cyp" ? "장벽 CYP3A4 비활성화 (자몽 푸라노쿠마린)" : it?.kind === "pgp" ? "P-당단백 배출 펌프 차단"
        : "소장 상피 세포막 통과 (수동 확산)",
      sub: it ? "분해·배출되지 않은 약이 더 많이 혈액으로 들어갑니다" : "지질 이중층을 지나 문맥 혈류로", sev: it?.sev || "normal" };
  }
  if (stage.id === "liver") {
    const c = (stage.clashes || []).find((x) => x.sev !== "low") || (stage.clashes || [])[0];
    if (c) {
      return { scene: <EnzymeScene a={mk(c.perpetrator)} b={mk(c.victim)} heme name={c.enzyme} inhibit={c.action === "inhibitor"}
        induce={c.action === "inducer"} />,
        caption: `${c.enzyme} · ${nameOf(c.perpetrator)} ${c.action === "inhibitor" ? "활성부위 결합(억제)" : "효소 유도"}`,
        sub: c.action === "inhibitor" ? `${nameOf(c.victim)}: 헴 활성부위에 접근 불가 → 대사 차단 → 축적` : `${nameOf(c.victim)} 분해 가속 → 효과 감소`,
        sev: c.sev };
    }
    const e = (stage.enzymes || [])[0];
    const sub = e && [...e.substrates, ...e.activates][0];
    if (sub) {
      return { scene: <EnzymeScene a={mk(sub)} heme name={e.name} />, caption: `${e.name} · ${nameOf(sub)} 대사`,
        sub: "헴(Fe) 활성부위에서 산화 → 대사체로 바뀌어 배출 준비", sev: "normal" };
    }
    return null;
  }
  if (stage.id === "target") {
    const over = (stage.overlaps || [])[0];
    const pick = over ? (stage.binds || []).filter((b) => b.site === over.site && over.drugs.includes(b.drug))
      : (stage.binds || []).slice(0, 1);
    const seen = new Set();
    const items = pick.filter((b) => !seen.has(b.drug) && seen.add(b.drug)).slice(0, 2)
      .map((b) => ({ ...mk(b.drug, 0.42), kind: b.protein, label: b.label, action: b.action }));
    if (!items.length) return null;
    return { scene: <ReceptorScene items={items} />,
      caption: items.map((i) => `${i.name} → ${i.label}`).join(" · "),
      sub: over ? `${pathway.sites[over.site]}: 같은 계통에 동시 작용 → 효과 중첩` : items[0].action, sev: over ? "mid" : "normal" };
  }
  return null;
}

export default function MolecularView({ plan }) {
  if (!plan) return null;
  return (
    <Canvas camera={{ position: [0, 0.5, 6.2], fov: 36 }} dpr={[1, 2]} gl={{ antialias: true }}>
      <color attach="background" args={["#020a10"]} />
      <ambientLight intensity={0.55} />
      <directionalLight position={[3, 4, 5]} intensity={1.6} />
      <pointLight position={[-4, -2, 3]} intensity={30} color="#00e5ff" />
      <pointLight position={[4, 2, -2]} intensity={20} color="#ff4fd8" />
      <group>{plan.scene}</group>
      <Bloom strength={0.45} radius={0.4} threshold={0.55} />
    </Canvas>
  );
}
