import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { STAGES } from "./stages";

/**
 * The cascade as depth.
 *
 * Five plates recede into Z, one per stage. The token — the printed lab name —
 * travels back through the plates that do not match it and comes to rest on
 * the one that does. Depth is the argument: you can see how far a name has to
 * travel before something catches it, and that the model plate is the last and
 * furthest.
 *
 * Materials are matte and lit from one side, like laboratory glass on a bench.
 * No emissive, no bloom: a plate is legible because light falls on it.
 */

const GAP = 1.0;
const PLATE_W = 2.5;
const PLATE_H = 1.55;

function Plate({ index, state, active, onHover, onLeave }) {
  const mesh = useRef();
  const target = useRef(0);

  // Passed plates recede and fade; the catching plate holds forward and opaque.
  const restOpacity =
    state === "caught" ? 0.96 : state === "passed" ? 0.13 : 0.3;

  useFrame((_, dt) => {
    if (!mesh.current) return;
    const wanted = (active ? 0.16 : 0) + (state === "caught" ? 0.1 : 0);
    target.current += (wanted - target.current) * Math.min(1, dt * 7);
    mesh.current.position.z = -index * GAP + target.current;

    const m = mesh.current.material;
    const o = restOpacity + (active ? 0.22 : 0);
    m.opacity += (o - m.opacity) * Math.min(1, dt * 7);
  });

  const color =
    state === "caught" ? "#1b3a6b" : active ? "#5b6675" : "#9a9aa1";

  return (
    <mesh
      ref={mesh}
      position={[0, 0, -index * GAP]}
      renderOrder={100 - index}
      onPointerOver={(e) => { e.stopPropagation(); onHover(); }}
      onPointerOut={onLeave}
    >
      <boxGeometry args={[PLATE_W, PLATE_H, 0.035]} />
      <meshStandardMaterial
        color={color}
        transparent
        opacity={restOpacity}
        roughness={0.55}
        metalness={0.05}
        side={THREE.DoubleSide}
        /* Transparent surfaces that overlap must not write depth: with
           depthWrite on, whichever drew first occludes the rest and the
           winner flips as the camera moves, which reads as flicker the
           moment the cursor drives the rig. */
        depthWrite={false}
      />
    </mesh>
  );
}

/** Thin outline so a nearly-transparent plate still reads as a surface. */
function PlateEdge({ index, state }) {
  const geo = useMemo(
    () => new THREE.EdgesGeometry(new THREE.BoxGeometry(PLATE_W, PLATE_H, 0.035)),
    []
  );
  return (
    <lineSegments geometry={geo} position={[0, 0, -index * GAP]} renderOrder={200 - index}>
      <lineBasicMaterial
        depthWrite={false}
        polygonOffset
        polygonOffsetFactor={-1}
        color={state === "caught" ? "#1b3a6b" : "#111112"}
        transparent
        opacity={state === "caught" ? 0.9 : 0.22}
      />
    </lineSegments>
  );
}

/** The printed name, travelling. */
function Token({ depth, resolved }) {
  const ref = useRef();
  useFrame((_, dt) => {
    if (!ref.current) return;
    const z = -depth * GAP + 0.42;
    ref.current.position.z += (z - ref.current.position.z) * Math.min(1, dt * 3.4);
  });
  return (
    <mesh ref={ref} position={[0, 0, 0.42]} castShadow>
      <boxGeometry args={[0.92, 0.3, 0.12]} />
      <meshStandardMaterial
        color={resolved ? "#1b3a6b" : "#a8442c"}
        roughness={0.35}
        metalness={0.1}
      />
    </mesh>
  );
}

/** Cursor parallax, damped. The scene leans; it never spins. */
function Rig({ enabled }) {
  const { camera, pointer } = useThree();
  useFrame((_, dt) => {
    if (!enabled) return;
    const k = Math.min(1, dt * 2.2);
    camera.position.x += (pointer.x * 0.7 - camera.position.x) * k;
    camera.position.y += (0.95 + pointer.y * 0.32 - camera.position.y) * k;
    camera.lookAt(0, -0.05, -2.0);
  });
  return null;
}

export default function CascadeScene({ path, depth, resolved, activeId, onHover }) {
  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0.5, 0.95, 6.2], fov: 34 }}
      gl={{ antialias: true }}
      style={{ touchAction: "pan-y" }}
    >
      <color attach="background" args={["#fafaf8"]} />
      {/* One key light and a soft fill: bench lighting, not a light show. */}
      <ambientLight intensity={0.72} />
      <directionalLight position={[3.2, 5, 4]} intensity={1.5} />
      <directionalLight position={[-4, 1, -2]} intensity={0.32} />

      <group rotation={[0, -0.34, 0]} position={[0, -0.1, 0]}>
        {path.map((s, i) => (
          <group key={s.id}>
            <Plate
              index={i}
              state={s.state}
              active={activeId === s.id}
              onHover={() => onHover(s.id)}
              onLeave={() => onHover(null)}
            />
            <PlateEdge index={i} state={s.state} />
          </group>
        ))}
        <Token depth={depth} resolved={resolved} />
      </group>

      <Rig enabled={!reduced} />
    </Canvas>
  );
}
