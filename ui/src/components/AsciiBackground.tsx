import { useEffect, useRef } from 'react'

/*
  Very subtle background — sparse trading-themed characters
  that drift slowly. Readable symbols: $ % 0-9 arrows, SOL, etc.
  Mouse creates a gentle amber reveal in a small radius.
  Intentionally quiet so the fire animation is the star.
*/

const SYMBOLS = '$$%%00112233..::++-->><<^^SOL'
const CELL = 28  // large grid = sparse

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

export default function AsciiBackground() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const noise = createNoise()
    let raf: number
    let t = 0
    let mx = -1, my = -1

    // pre-pick a stable character per cell so they don't scramble
    const charMap: string[] = []
    for (let i = 0; i < 20000; i++) {
      charMap.push(SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)])
    }

    let dpr = 1
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio, 2)
      canvas.width = window.innerWidth * dpr
      canvas.height = window.innerHeight * dpr
      canvas.style.width = window.innerWidth + 'px'
      canvas.style.height = window.innerHeight + 'px'
    }
    resize()
    window.addEventListener('resize', resize)

    const onMouse = (e: MouseEvent) => {
      mx = e.clientX / window.innerWidth
      my = e.clientY / window.innerHeight
    }
    window.addEventListener('mousemove', onMouse)

    const draw = () => {
      t += 0.001  // very slow drift
      const w = window.innerWidth
      const h = window.innerHeight

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      ctx.font = `11px 'JetBrains Mono', monospace`
      ctx.textBaseline = 'middle'
      ctx.textAlign = 'center'

      const cols = Math.ceil(w / CELL)
      const rows = Math.ceil(h / CELL)

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const px = col * CELL + CELL / 2
          const py = row * CELL + CELL / 2
          const nx = col / cols
          const ny = row / rows
          const ci = row * cols + col

          // noise decides if this cell is visible at all (sparse)
          const n = noise(nx * 3, ny * 3, t)
          if (n < 0.58) continue  // only ~40% of cells show

          // mouse proximity — gentle reveal
          let mouseAlpha = 0
          if (mx >= 0) {
            const mdx = nx - mx
            const mdy = ny - my
            const md = Math.sqrt(mdx * mdx + mdy * mdy)
            mouseAlpha = Math.max(0, 1 - md * 5) * 0.2  // small radius, subtle
          }

          const baseAlpha = 0.06 + (n - 0.58) * 0.15  // very dim
          const alpha = Math.min(0.3, baseAlpha + mouseAlpha)

          const char = charMap[ci % charMap.length]

          if (mouseAlpha > 0.02) {
            ctx.fillStyle = `rgba(232, 168, 73, ${alpha})`  // amber near mouse
          } else {
            ctx.fillStyle = `rgba(100, 100, 110, ${alpha})`  // gray otherwise
          }
          ctx.fillText(char, px, py)
        }
      }

      raf = requestAnimationFrame(draw)
    }
    draw()

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      window.removeEventListener('mousemove', onMouse)
    }
  }, [])

  return (
    <canvas
      ref={ref}
      className="fixed inset-0"
      style={{ zIndex: 0, pointerEvents: 'none' }}
    />
  )
}
