import { useEffect, useRef, useCallback } from 'react'

/*
  ASCII campfire with textured characters, forked prongs, concentric
  color zones, crossed logs, stones, and floating ember particles.
  All rendering via fillText — proper ASCII look.
*/

// Character sets by zone — each zone has its own texture
const CORE_CHARS = '@#W&%'       // dense, hot center
const BRIGHT_CHARS = '*#X%$'     // bright yellow zone
const MID_CHARS = 'x+=$~'        // orange mid
const OUTER_CHARS = ':;-~.'      // dim red edges/tips
const SMOKE_CHARS = '.,\' `'     // wispy top
const WOOD_CHARS = '=#HXx%'
const STONE_CHARS = 'oO0@'
const EMBER_CHARS = '.,*+\'`~^'

function createNoise() {
  const p = new Uint8Array(512)
  const perm = new Uint8Array(256)
  for (let i = 0; i < 256; i++) perm[i] = i
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[perm[i], perm[j]] = [perm[j], perm[i]]
  }
  for (let i = 0; i < 512; i++) p[i] = perm[i & 255]
  const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10)
  const lerp = (a: number, b: number, t: number) => a + t * (b - a)
  const grad3 = (hash: number, x: number, y: number, z: number) => {
    const h = hash & 15
    const u = h < 8 ? x : y
    const v = h < 4 ? y : h === 12 || h === 14 ? x : z
    return ((h & 1) ? -u : u) + ((h & 2) ? -v : v)
  }
  return (x: number, y: number, z: number): number => {
    const X = Math.floor(x) & 255, Y = Math.floor(y) & 255, Z = Math.floor(z) & 255
    x -= Math.floor(x); y -= Math.floor(y); z -= Math.floor(z)
    const u = fade(x), v = fade(y), w = fade(z)
    const A = p[X] + Y, AA = p[A] + Z, AB = p[A + 1] + Z
    const B = p[X + 1] + Y, BA = p[B] + Z, BB = p[B + 1] + Z
    return (lerp(
      lerp(lerp(grad3(p[AA], x, y, z), grad3(p[BA], x - 1, y, z), u),
           lerp(grad3(p[AB], x, y - 1, z), grad3(p[BB], x - 1, y - 1, z), u), v),
      lerp(lerp(grad3(p[AA + 1], x, y, z - 1), grad3(p[BA + 1], x - 1, y, z - 1), u),
           lerp(grad3(p[AB + 1], x, y - 1, z - 1), grad3(p[BB + 1], x - 1, y - 1, z - 1), u), v), w
    ) + 1) / 2
  }
}

function pickChar(chars: string, noiseVal: number): string {
  return chars[Math.floor(noiseVal * chars.length) % chars.length]
}

interface Spark {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; bright: number
}

interface Ember {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; char: string; size: number
}

interface Props { cols?: number; rows?: number }

export default function AsciiFire({ cols = 70, rows = 40 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const mouseRef = useRef({ x: 0.5, y: 0.5, active: false })
  const animRef = useRef<number>(0)

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    mouseRef.current = {
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
      active: true,
    }
  }, [])

  const handleMouseLeave = useCallback(() => {
    mouseRef.current.active = false
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    if (!ctx) return

    const noise = createNoise()
    const startTime = performance.now()

    const fontSize = 13
    const cellW = fontSize * 0.62
    const cellH = fontSize * 1.1
    const pxW = cols * cellW
    const pxH = rows * cellH

    const dpr = Math.min(window.devicePixelRatio, 2)
    canvas.width = pxW * dpr
    canvas.height = pxH * dpr
    canvas.style.width = pxW + 'px'
    canvas.style.height = pxH + 'px'

    const cxCol = Math.floor(cols / 2)
    const fireBase = rows - 8
    const fireMaxH = 24
    const fireHalfW = 20

    const sparks: Spark[] = []
    const embers: Ember[] = []
    let sparkBurstDone = false

    function render(now: number) {
      const elapsed = (now - startTime) / 1000
      const t = elapsed
      const mouse = mouseRef.current
      const windX = mouse.active ? (mouse.x - 0.5) * 4 : 0

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, pxW, pxH)
      ctx.font = `${fontSize}px 'JetBrains Mono', 'Fira Code', 'Consolas', monospace`
      ctx.textBaseline = 'top'

      const progress = Math.min(1, Math.max(0, (elapsed - 1.5) / 3))

      // == SPARK BURST ==
      if (elapsed > 0.6 && !sparkBurstDone) {
        sparkBurstDone = true
        for (let i = 0; i < 70; i++) {
          const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.2
          const speed = 2 + Math.random() * 5
          sparks.push({
            x: cxCol * cellW + (Math.random() - 0.5) * 30,
            y: fireBase * cellH + (Math.random() - 0.5) * 10,
            vx: Math.cos(angle) * speed * 1.5,
            vy: Math.sin(angle) * speed * 2,
            life: 0, maxLife: 15 + Math.random() * 35,
            bright: 0.5 + Math.random() * 0.5,
          })
        }
      }

      // == STONES ==
      const stoneY = fireBase + 3
      const stonePositions = [-22, -20, -17, -13, 13, 17, 20, 22]
      for (const sx of stonePositions) {
        const sc = cxCol + sx
        if (sc < 0 || sc >= cols) continue
        const sw = 2 + Math.floor(noise(sx * 0.7, 0, 0) * 2)
        for (let dx = 0; dx < sw; dx++) {
          for (let dy = 0; dy < 2; dy++) {
            const c = sc + dx, r = stoneY + dy
            if (c >= 0 && c < cols && r < rows) {
              const b = 0.25 + noise(c * 0.5, r * 0.5, 0) * 0.2
              const ch = pickChar(STONE_CHARS, noise(c * 0.3, r * 0.4, 0))
              ctx.fillStyle = `rgb(${65 + b * 70 | 0},${58 + b * 55 | 0},${52 + b * 45 | 0})`
              ctx.fillText(ch, c * cellW, r * cellH)
            }
          }
        }
      }

      // == CROSSED LOGS ==
      for (let i = -16; i <= 16; i++) {
        const glow = progress * Math.max(0, 1 - Math.abs(i) / 8) * 0.3
        const ch = pickChar(WOOD_CHARS, noise(i * 0.3, 0, 0))
        // Log 1
        const r1 = fireBase + 2 - Math.round(i * 0.15), c1 = cxCol + i
        if (c1 >= 0 && c1 < cols && r1 >= 0 && r1 < rows) {
          ctx.fillStyle = `rgb(${52 + glow * 200 | 0},${30 + glow * 60 | 0},${14 + glow * 20 | 0})`
          ctx.fillText(ch, c1 * cellW, r1 * cellH)
        }
        // Log 2
        const r2 = fireBase + 2 + Math.round(i * 0.15), c2 = cxCol + i
        if (c2 >= 0 && c2 < cols && r2 >= 0 && r2 < rows) {
          ctx.fillStyle = `rgb(${48 + glow * 180 | 0},${26 + glow * 50 | 0},${12 + glow * 15 | 0})`
          ctx.fillText(ch, c2 * cellW, r2 * cellH)
        }
      }
      // Horizontal log
      for (let i = -14; i <= 14; i++) {
        const c = cxCol + i, r = fireBase + 3
        if (c >= 0 && c < cols && r < rows) {
          const glow = progress * Math.max(0, 1 - Math.abs(i) / 6) * 0.2
          const ch = pickChar(WOOD_CHARS, noise(c * 0.4, 0, 5))
          ctx.fillStyle = `rgb(${58 + glow * 150 | 0},${34 + glow * 50 | 0},${16 + glow * 15 | 0})`
          ctx.fillText(ch, c * cellW, r * cellH)
        }
      }

      // == FIRE BODY (ASCII characters, concentric color zones) ==
      if (elapsed > 1.5) {
        const prongCenters = [-7, 0, 6]
        const prongHeights = [0.8, 1.0, 0.7]

        for (let col = cxCol - fireHalfW - 2; col <= cxCol + fireHalfW + 2; col++) {
          if (col < 0 || col >= cols) continue
          const relX = (col - cxCol) / fireHalfW
          const baseWidth = 1 - relX * relX
          if (baseWidth <= 0) continue

          // Forked prong heights
          let maxH = 0
          for (let pi = 0; pi < 3; pi++) {
            const prongX = prongCenters[pi] / fireHalfW
            const distToProng = Math.abs(relX - prongX)
            const prongInfluence = Math.exp(-distToProng * distToProng * 10)
            maxH = Math.max(maxH, fireMaxH * prongHeights[pi] * prongInfluence)
          }
          maxH = Math.max(maxH, fireMaxH * 0.5 * baseWidth)

          const edgeNoise = noise(col * 0.3, t * 2, 0) * 3
          const tipNoise = noise(col * 0.15, t * 1.3, 10) * 4
          const totalH = Math.floor((maxH + edgeNoise + tipNoise) * progress)
          if (totalH <= 0) continue

          for (let dy = 0; dy < totalH; dy++) {
            const row = fireBase - dy
            if (row < 0) break

            const heightRatio = dy / fireMaxH
            const wCol = Math.round(col + windX * heightRatio * 0.8)
            if (wCol < 0 || wCol >= cols) continue

            const widthAtHeight = fireHalfW * Math.max(0.1, 1 - heightRatio * 0.7) * baseWidth
            const distFromAxis = Math.abs(wCol - cxCol)
            const insideness = Math.max(0, 1 - distFromAxis / Math.max(1, widthAtHeight))

            // Choppy gaps
            const gapNoise = noise(col * 0.5, dy * 0.4, t * 3)
            if (gapNoise < 0.05 + (1 - insideness) * 0.2 + heightRatio * 0.15 && heightRatio > 0.15) continue

            const flicker = noise(col * 0.4 + dy * 0.3, t * 4, dy * 0.15)
            const coreness = insideness * (1 - heightRatio * 0.4) * (0.7 + flicker * 0.3)

            // Pick character set + color by zone
            let char: string
            let r: number, g: number, b: number, a: number
            const charSeed = noise(col * 0.7, dy * 0.5, t * 2)

            if (coreness > 0.75) {
              char = pickChar(CORE_CHARS, charSeed)
              r = 255; g = 245; b = 200; a = 1
            } else if (coreness > 0.55) {
              char = pickChar(BRIGHT_CHARS, charSeed)
              const s = (coreness - 0.55) / 0.2
              r = 255; g = 195 + s * 50; b = 60 + s * 140; a = 1
            } else if (coreness > 0.3) {
              char = pickChar(MID_CHARS, charSeed)
              const s = (coreness - 0.3) / 0.25
              r = 220 + s * 35; g = 100 + s * 95; b = 15 + s * 45; a = 0.95
            } else if (coreness > 0.12) {
              char = pickChar(OUTER_CHARS, charSeed)
              const s = (coreness - 0.12) / 0.18
              r = 160 + s * 60; g = 30 + s * 70; b = 5 + s * 10; a = 0.85
            } else if (coreness > 0.03) {
              char = pickChar(SMOKE_CHARS, charSeed)
              const s = coreness / 0.12
              r = 90 + s * 70; g = 15 + s * 15; b = 3 + s * 2; a = 0.4 + s * 0.4
            } else {
              continue
            }

            ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${a})`
            ctx.fillText(char, wCol * cellW, row * cellH)
          }
        }

        // Inner core overlay — extra bright
        const coreH = Math.floor(7 * progress)
        const coreW = Math.floor(5 * progress)
        for (let dy = 0; dy < coreH; dy++) {
          const row = fireBase - dy
          if (row < 0) break
          const hr = dy / coreH
          const w = Math.max(1, Math.round(coreW * (1 - hr * 0.7)))
          for (let dx = -w; dx <= w; dx++) {
            const c = cxCol + dx + Math.round(windX * hr * 0.3)
            if (c < 0 || c >= cols) continue
            const flick = noise(c * 0.5, t * 5, dy * 0.3)
            if (flick < 0.25) continue
            const brightness = (1 - hr) * (0.8 + flick * 0.2)
            const ch = pickChar(CORE_CHARS, flick)
            ctx.fillStyle = `rgba(255,${240 + brightness * 15 | 0},${195 + brightness * 60 | 0},${brightness})`
            ctx.fillText(ch, c * cellW, row * cellH)
          }
        }
      }

      // == INITIAL SPARKS ==
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i]
        s.x += s.vx * 0.4; s.y += s.vy * 0.4
        s.vy += 0.12; s.vx *= 0.97; s.life++
        if (s.life > s.maxLife) { sparks.splice(i, 1); continue }
        const rem = 1 - s.life / s.maxLife
        const b = rem * s.bright
        ctx.fillStyle = `rgba(${255 * b | 0},${220 * b | 0},${100 * b | 0},${b})`
        ctx.fillText('*', s.x, s.y)
      }

      // == EMBERS — separate particle system, always drifting up ==
      if (elapsed > 3) {
        // Spawn embers
        if (Math.random() < 0.5) {
          const spawnX = cxCol + (Math.random() - 0.5) * fireHalfW * 1.5
          const spawnY = fireBase - 5 - Math.random() * 15
          embers.push({
            x: spawnX * cellW,
            y: spawnY * cellH,
            vx: (Math.random() - 0.5) * 1.0 + windX * 0.3,
            vy: -0.6 - Math.random() * 1.8,
            life: 0,
            maxLife: 50 + Math.random() * 70,
            char: EMBER_CHARS[Math.floor(Math.random() * EMBER_CHARS.length)],
            size: fontSize - 2 + Math.floor(Math.random() * 4),
          })
        }
      }

      // Update + render embers
      for (let i = embers.length - 1; i >= 0; i--) {
        const e = embers[i]
        e.x += e.vx
        e.y += e.vy
        e.vx += (Math.random() - 0.5) * 0.15  // lateral drift
        e.vy -= 0.008                           // gentle upward pull
        e.life++

        if (e.life > e.maxLife || e.y < -20 || e.x < -20 || e.x > pxW + 20) {
          embers.splice(i, 1)
          continue
        }

        const rem = 1 - e.life / e.maxLife
        const fadePhase = rem > 0.7 ? 1 : rem / 0.7  // bright for 70% of life, then fade

        // Change character occasionally as it drifts
        if (Math.random() < 0.03) {
          e.char = EMBER_CHARS[Math.floor(Math.random() * EMBER_CHARS.length)]
        }

        // Color: starts amber, fades to dark red, then dim
        const r = 200 + fadePhase * 55
        const g = 80 + fadePhase * 80
        const b = 10 + fadePhase * 30
        const a = fadePhase * 0.9

        ctx.font = `${e.size}px 'JetBrains Mono', monospace`
        ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${a})`
        ctx.fillText(e.char, e.x, e.y)
      }

      // Reset font for next frame
      ctx.font = `${fontSize}px 'JetBrains Mono', 'Fira Code', 'Consolas', monospace`

      // == AMBIENT GLOW ==
      if (elapsed > 2) {
        const gi = Math.min(1, (elapsed - 2) / 3) * 0.1
        const grd = ctx.createRadialGradient(
          cxCol * cellW, fireBase * cellH, 0,
          cxCol * cellW, fireBase * cellH, fireHalfW * cellW * 1.5
        )
        grd.addColorStop(0, `rgba(255, 180, 60, ${gi})`)
        grd.addColorStop(0.5, `rgba(200, 100, 20, ${gi * 0.4})`)
        grd.addColorStop(1, 'rgba(200, 80, 20, 0)')
        ctx.globalCompositeOperation = 'lighter'
        ctx.fillStyle = grd
        ctx.fillRect(0, 0, pxW, pxH)
        ctx.globalCompositeOperation = 'source-over'
      }

      animRef.current = requestAnimationFrame(render)
    }

    animRef.current = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animRef.current)
  }, [cols, rows])

  return (
    <div
      ref={containerRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{ position: 'relative', display: 'inline-block', cursor: 'crosshair' }}
    >
      <canvas ref={canvasRef} style={{ display: 'block' }} />
    </div>
  )
}
