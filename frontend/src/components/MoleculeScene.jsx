import React, { useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, OrbitControls, Stars } from "@react-three/drei";
import * as THREE from "three";

const CPK = {
  C: "#7fdbff", H: "#e8f6ff", N: "#4d6bff", O: "#ff3d6e", S: "#ffd23f", P: "#ff9f1c", F: "#9dff6a",
  Cl: "#3dff8f", Br: "#ff7a3d", I: "#b46bff", Na: "#c084fc", K: "#c084fc", Fe: "#ff8c42", Li: "#f472b6",
};
const RADIUS = { H: 0.22 };
const IMPACT = 1.15; // seconds from start of the collision to the impact flash
const now = () => performance.now() / 1000;

const easeOutBack = (t) => 1 + 2.4 * (t - 1) ** 3 + 1.4 * (t - 1) ** 2;
const easeInCubic = (t) => t * t * t;

/** Slot of molecule k out of n around the stage center. */
function slot(k, n, cx, cy = 0) {
  if (n === 1) return new THREE.Vector3(cx, cy, 0);
  if (n === 2) return new THREE.Vector3(cx + (k === 0 ? -2.5 : 2.5), cy, 0);
  const a = (k / n) * Math.PI * 2 + Math.PI / 2;
  const r = n <= 4 ? 2.7 : 3.0;
  return new THREE.Vector3(cx + Math.cos(a) * r * 1.1, cy + Math.sin(a) * r * 0.8, 0);
}

function Molecule({ mol, index, count, cx, anim, level, label }) {
  const group = useRef();
  const inner = useRef();
  const atomsRef = useRef();
  const bondsRef = useRef();
  const born = useMemo(now, [mol.key]);

  const { atoms, bonds, scale, labelY } = useMemo(() => {
    const atoms = mol.atoms.map(([el, x, y, z]) => ({ el, p: new THREE.Vector3(x, y, z) }));
    const maxR = Math.max(1, ...atoms.map((a) => a.p.length()));
    const target = count <= 2 ? 1.9 : count <= 4 ? 1.5 : count <= 6 ? 1.15 : 0.95;
    const scale = Math.min(0.85, target / maxR);
    // the label lives inside the scaled group: place it just below the molecule's world-space radius
    return { atoms, bonds: mol.bonds, scale, labelY: -(Math.min(target, maxR * scale) + 0.55) / scale };
  }, [mol, count]);

  useLayoutEffect(() => {
    const m = new THREE.Matrix4();
    const c = new THREE.Color();
    atoms.forEach((a, i) => {
      const r = RADIUS[a.el] ?? 0.36;
      m.compose(a.p, new THREE.Quaternion(), new THREE.Vector3(r, r, r));
      atomsRef.current.setMatrixAt(i, m);
      atomsRef.current.setColorAt(i, c.set(CPK[a.el] || "#ff66cc"));
    });
    atomsRef.current.instanceMatrix.needsUpdate = true;
    if (atomsRef.current.instanceColor) atomsRef.current.instanceColor.needsUpdate = true;
    const up = new THREE.Vector3(0, 1, 0);
    bonds.forEach(([i, j, order], k) => {
      const a = atoms[i].p, b = atoms[j].p;
      const mid = a.clone().add(b).multiplyScalar(0.5);
      const dir = b.clone().sub(a);
      const q = new THREE.Quaternion().setFromUnitVectors(up, dir.clone().normalize());
      const w = order >= 2 ? 1.6 : order > 1 ? 1.3 : 1;
      m.compose(mid, q, new THREE.Vector3(w, dir.length(), w));
      bondsRef.current.setMatrixAt(k, m);
    });
    bondsRef.current.instanceMatrix.needsUpdate = true;
  }, [atoms, bonds]);

  // one Vector3 per slot: a new one on every render made react-three-fiber reset the group to `home` (dropping the
  // float offset) whenever the page re-rendered -> visible up/down twitching
  const home = useMemo(() => slot(index, count, cx), [index, count, cx]);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  const center = useMemo(() => new THREE.Vector3(cx, 0, 0), [cx]);
  useFrame((_, dt) => {
    const t = now();
    const g = group.current;
    const s = scale * easeOutBack(Math.min(1, (t - born) / 0.9));
    inner.current.rotation.y += dt * (anim.current.phase === "colliding" ? 3.5 : 0.55);
    inner.current.rotation.x = Math.sin(t * 0.4 + index) * 0.35;
    const { phase, t0 } = anim.current;
    if (phase === "colliding") {
      const p = Math.min(1, (t - t0) / IMPACT);
      tmp.copy(home).lerp(center, easeInCubic(p) * 0.92);
      g.position.copy(tmp);
      const shake = p > 0.7 ? (p - 0.7) * 0.25 : 0;
      g.position.x += (Math.random() - 0.5) * shake;
      g.position.y += (Math.random() - 0.5) * shake;
      g.scale.setScalar(s * (1 - 0.25 * easeInCubic(p)));
    } else {
      // after the impact the molecules spring back to their slots and pulse with the risk colour
      tmp.copy(home);
      tmp.y += Math.sin(t * 0.9 + index * 1.7) * 0.18;
      g.position.lerp(tmp, phase === "done" ? Math.min(1, dt * 3) : 1);
      const pulse = phase === "done" && level === "높음" ? 1 + Math.sin(t * 6) * 0.03 : 1;
      g.scale.setScalar(s * pulse);
    }
  });

  const glow = level === "높음" ? "#ff2d55" : level === "주의" ? "#ffb020" : "#00e5ff";
  return (
    <group ref={group} position={home}>
      <group ref={inner}>
        <instancedMesh ref={atomsRef} args={[null, null, atoms.length]}>
          <sphereGeometry args={[1, 24, 16]} />
          <meshStandardMaterial roughness={0.25} metalness={0.3} emissive={glow} emissiveIntensity={0.18} />
        </instancedMesh>
        <instancedMesh ref={bondsRef} args={[null, null, Math.max(1, bonds.length)]}>
          <cylinderGeometry args={[0.075, 0.075, 1, 10]} />
          <meshStandardMaterial color="#9fb3c8" roughness={0.4} metalness={0.5} emissive="#00e5ff" emissiveIntensity={0.08} />
        </instancedMesh>
      </group>
      <Html position={[0, labelY, 0]} center distanceFactor={11} zIndexRange={[5, 0]}>
        <div className={`mol-label ${mol.known ? "" : "novel"}`}>
          <b>{label}</b>
          {mol.props?.formula && <small>{mol.props.formula} · MW {mol.props.mw} · logP {mol.props.logp}</small>}
        </div>
      </Html>
    </group>
  );
}

function ImpactFlash({ anim, cx, color }) {
  const ref = useRef();
  const ring = useRef();
  useFrame(() => {
    const { phase, t0 } = anim.current;
    const dt = now() - t0 - IMPACT;
    const on = (phase === "colliding" || phase === "done") && dt > 0 && dt < 1.4;
    ref.current.visible = ring.current.visible = on;
    if (!on) return;
    ref.current.scale.setScalar(0.3 + dt * 7);
    ref.current.material.opacity = Math.max(0, 0.85 - dt * 0.9);
    ring.current.scale.setScalar(0.5 + dt * 11);
    ring.current.material.opacity = Math.max(0, 0.9 - dt * 0.75);
  });
  return (
    <group position={[cx, 0, 0]}>
      <mesh ref={ref} visible={false}>
        <sphereGeometry args={[1, 32, 16]} />
        <meshBasicMaterial color={color} transparent blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <mesh ref={ring} visible={false}>
        <ringGeometry args={[0.92, 1, 96]} />
        <meshBasicMaterial color={color} transparent side={THREE.DoubleSide} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
    </group>
  );
}

const N_PARTICLES = 2400;
function DataStream({ anim, cx, targetX, color }) {
  const pts = useRef();
  const state = useMemo(() => {
    const pos = new Float32Array(N_PARTICLES * 3).fill(9999);
    const vel = new Float32Array(N_PARTICLES * 3);
    const delay = new Float32Array(N_PARTICLES);
    return { pos, vel, delay, started: -1 };
  }, []);
  useFrame((_, dt) => {
    const { phase, t0 } = anim.current;
    const t = now() - t0 - IMPACT;
    const { pos, vel, delay } = state;
    if ((phase === "colliding" || phase === "done") && t > 0 && state.started !== t0) {
      state.started = t0;
      for (let i = 0; i < N_PARTICLES; i++) {
        const a = Math.random() * Math.PI * 2;
        const sp = 1 + Math.random() * 4;
        pos[i * 3] = cx; pos[i * 3 + 1] = 0; pos[i * 3 + 2] = 0;
        vel[i * 3] = Math.cos(a) * sp; vel[i * 3 + 1] = Math.sin(a) * sp; vel[i * 3 + 2] = (Math.random() - 0.5) * 3;
        delay[i] = Math.random() * 1.6;
      }
    }
    if (state.started !== t0) return;
    for (let i = 0; i < N_PARTICLES; i++) {
      if (t < delay[i]) continue;
      // burst outward, then get pulled into a stream towards the result panel
      const k = i * 3;
      const ty = (((i * 7919) % 100) / 100 - 0.5) * 7;
      vel[k] += ((targetX - pos[k]) * 1.6 - vel[k] * 0.6) * dt * 1.4;
      vel[k + 1] += ((ty - pos[k + 1]) * 1.2 - vel[k + 1] * 0.6) * dt * 1.4;
      vel[k + 2] += (-pos[k + 2] - vel[k + 2] * 0.5) * dt;
      pos[k] += vel[k] * dt; pos[k + 1] += vel[k + 1] * dt; pos[k + 2] += vel[k + 2] * dt;
      if (pos[k] > targetX - 0.3 || t > 6) pos[k] = 9999; // absorbed by the panel
    }
    pts.current.geometry.attributes.position.needsUpdate = true;
  });
  return (
    <points ref={pts} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={N_PARTICLES} array={state.pos} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial size={0.07} color={color} transparent opacity={0.9} blending={THREE.AdditiveBlending} depthWrite={false} sizeAttenuation />
    </points>
  );
}

function Stage({ molecules, anim, layout, level, pairLevelByKey }) {
  const { viewport } = useThree();
  const cx = layout.full ? (layout.centerFrac - 0.5) * viewport.width : 0;
  const targetX = layout.full ? (layout.targetFrac - 0.5) * viewport.width : viewport.width / 2 + 1;
  const color = level === "높음" ? "#ff2d55" : level === "주의" ? "#ffb020" : "#00e5ff";
  return (
    <>
      <ambientLight intensity={0.35} />
      <pointLight position={[cx + 6, 6, 8]} intensity={140} color="#00e5ff" />
      <pointLight position={[cx - 6, -4, 6]} intensity={90} color="#ff2d95" />
      <directionalLight position={[0, 5, 10]} intensity={0.8} />
      <Stars radius={60} depth={40} count={2500} factor={3} fade speed={0.6} />
      <gridHelper args={[60, 60, "#0b3b4a", "#071c24"]} position={[cx, -4.2, 0]} />
      {/* lift the stage above the drug search bar in the full-screen layout */}
      <group position={[0, layout.full ? 1.0 : 0, 0]}>
        {molecules.filter((m) => m.atoms?.length).map((m, i, arr) => (
          <Molecule key={m.key} mol={m} index={i} count={arr.length} cx={cx} anim={anim}
            level={pairLevelByKey[m.key]} label={m.display} />
        ))}
        <ImpactFlash anim={anim} cx={cx} color={color} />
        <DataStream anim={anim} cx={cx} targetX={targetX} color={color} />
      </group>
    </>
  );
}

export default React.memo(MoleculeScene);

function MoleculeScene({ molecules, anim, layout, level, pairLevelByKey }) {
  return (
    <Canvas camera={{ position: [0, 0.6, 14], fov: 45 }} dpr={[1, 2]} gl={{ antialias: true }}>
      <color attach="background" args={["#02060a"]} />
      <fog attach="fog" args={["#02060a", 18, 48]} />
      <Stage molecules={molecules} anim={anim} layout={layout} level={level} pairLevelByKey={pairLevelByKey} />
      <OrbitControls enablePan={false} enableZoom={!layout.full} maxPolarAngle={Math.PI * 0.62} minPolarAngle={Math.PI * 0.3}
        enableRotate={!layout.full} />
    </Canvas>
  );
}
