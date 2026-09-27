import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import type { Rig } from './rig'

// A simple footballer built from primitives: boots, socks, shins, thighs, shorts,
// torso, arms, neck, head, hair. Faces local -z, 1.8 m tall. Limbs hang from
// pivot groups so animateRig() can swing them.

export interface Kit {
  shirt: string
  shorts: string
  socks: string
  number: string // number colour on the back
}

const SKIN = '#b98a6a'
const HAIR = '#241a14'
const BOOT = '#111111'

const HIP_Y = 0.92
const THIGH = 0.44
const SHIN = 0.44
const SHOULDER_Y = 1.42
const UPPER_ARM = 0.29
const FOREARM = 0.27

// Shared geometries: one set for every player on the pitch.
const geo = {
  thigh: new THREE.CapsuleGeometry(0.075, THIGH - 0.15, 4, 10),
  shin: new THREE.CapsuleGeometry(0.06, SHIN - 0.12, 4, 10),
  boot: new THREE.BoxGeometry(0.09, 0.07, 0.24),
  shorts: new THREE.CylinderGeometry(0.19, 0.22, 0.3, 14),
  torso: new THREE.CapsuleGeometry(0.17, 0.3, 6, 14),
  upperArm: new THREE.CapsuleGeometry(0.048, UPPER_ARM - 0.1, 4, 8),
  forearm: new THREE.CapsuleGeometry(0.04, FOREARM - 0.08, 4, 8),
  neck: new THREE.CylinderGeometry(0.055, 0.06, 0.1, 10),
  head: new THREE.SphereGeometry(0.11, 16, 12),
  hair: new THREE.SphereGeometry(0.117, 16, 8, 0, Math.PI * 2, 0, Math.PI * 0.55),
  back: new THREE.PlaneGeometry(0.26, 0.26),
  joint: new THREE.SphereGeometry(1, 10, 8), // scaled per joint to hide the gaps when limbs bend
}

const plain = (color: string) => new THREE.MeshStandardMaterial({ color, roughness: 0.8 })
const skinMat = plain(SKIN)
const hairMat = plain(HAIR)
const bootMat = plain(BOOT)

function numberTexture(n: number, color: string): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const ctx = c.getContext('2d')!
  ctx.font = 'bold 96px system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = color
  ctx.fillText(String(n), 64, 70)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

interface PlayerBodyProps {
  kit: Kit
  number: number
  rigRef: (rig: Rig | null) => void
}

// One leg: hip pivot -> thigh -> knee pivot -> shin, sock and boot.
function Leg({ side, kit, hip, knee }: { side: 1 | -1; kit: Mats; hip: (g: THREE.Group | null) => void; knee: (g: THREE.Group | null) => void }) {
  return (
    <group ref={hip} position={[side * 0.1, HIP_Y, 0]}>
      <mesh geometry={geo.thigh} material={skinMat} position={[0, -THIGH / 2, 0]} castShadow />
      <group ref={knee} position={[0, -THIGH, 0]}>
        <mesh geometry={geo.joint} material={skinMat} scale={0.066} />
        <mesh geometry={geo.shin} material={kit.socks} position={[0, -SHIN / 2, 0]} castShadow />
        <mesh geometry={geo.boot} material={bootMat} position={[0, -SHIN + 0.02, -0.06]} castShadow />
      </group>
    </group>
  )
}

function Arm({ side, kit, shoulder, elbow }: { side: 1 | -1; kit: Mats; shoulder: (g: THREE.Group | null) => void; elbow: (g: THREE.Group | null) => void }) {
  return (
    <group ref={shoulder} position={[side * 0.24, SHOULDER_Y, 0]} rotation-z={side * 0.12}>
      <mesh geometry={geo.joint} material={kit.shirt} scale={0.075} />
      <mesh geometry={geo.upperArm} material={kit.shirt} position={[0, -UPPER_ARM / 2, 0]} castShadow />
      <group ref={elbow} position={[0, -UPPER_ARM, 0]}>
        <mesh geometry={geo.joint} material={skinMat} scale={0.048} />
        <mesh geometry={geo.forearm} material={skinMat} position={[0, -FOREARM / 2, 0]} castShadow />
      </group>
    </group>
  )
}

interface Mats {
  shirt: THREE.MeshStandardMaterial
  shorts: THREE.MeshStandardMaterial
  socks: THREE.MeshStandardMaterial
}

export function PlayerBody({ kit, number, rigRef }: PlayerBodyProps) {
  const mats = useMemo<Mats>(() => ({ shirt: plain(kit.shirt), shorts: plain(kit.shorts), socks: plain(kit.socks) }), [kit])
  const numTex = useMemo(() => numberTexture(number, kit.number), [number, kit.number])
  useEffect(
    () => () => {
      mats.shirt.dispose()
      mats.shorts.dispose()
      mats.socks.dispose()
      numTex.dispose()
    },
    [mats, numTex],
  )

  // Collect the pivot groups into one Rig once they've all mounted.
  const parts: Partial<Rig> = {}
  const set = (k: keyof Rig) => (g: THREE.Group | null) => {
    parts[k] = g ?? undefined
    if (Object.keys(parts).length === 9 && Object.values(parts).every(Boolean)) rigRef(parts as Rig)
  }

  return (
    <group ref={set('body')}>
      <Leg side={-1} kit={mats} hip={set('hipL')} knee={set('kneeL')} />
      <Leg side={1} kit={mats} hip={set('hipR')} knee={set('kneeR')} />
      <mesh geometry={geo.shorts} material={mats.shorts} position={[0, HIP_Y - 0.01, 0]} castShadow />
      <mesh geometry={geo.torso} material={mats.shirt} position={[0, 1.2, 0]} scale={[1.15, 1, 0.72]} castShadow />
      {/* Number on the back (the body faces -z, so the back is +z). */}
      <mesh geometry={geo.back} position={[0, 1.24, 0.13]}>
        <meshBasicMaterial map={numTex} transparent depthWrite={false} />
      </mesh>
      <Arm side={-1} kit={mats} shoulder={set('shoulderL')} elbow={set('elbowL')} />
      <Arm side={1} kit={mats} shoulder={set('shoulderR')} elbow={set('elbowR')} />
      <mesh geometry={geo.neck} material={skinMat} position={[0, 1.5, 0]} />
      <mesh geometry={geo.head} material={skinMat} position={[0, 1.64, 0]} castShadow />
      <mesh geometry={geo.hair} material={hairMat} position={[0, 1.655, 0.01]} />
    </group>
  )
}
