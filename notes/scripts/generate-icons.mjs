/*
 * Rasterises the SVG sources in assets/ into the PNGs the web app manifest and
 * iOS need. The outputs are committed, so the Docker build never needs sharp.
 *
 *   node scripts/generate-icons.mjs
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const OUTPUTS = [
  { source: 'assets/icon.svg', out: 'public/icon-192.png', size: 192 },
  { source: 'assets/icon.svg', out: 'public/icon-512.png', size: 512 },
  { source: 'assets/icon.svg', out: 'public/apple-icon.png', size: 180 },
  { source: 'assets/icon.svg', out: 'public/favicon-32.png', size: 32 },
  { source: 'assets/icon-maskable.svg', out: 'public/icon-maskable-512.png', size: 512 },
]

await mkdir(resolve(root, 'public'), { recursive: true })

for (const { source, out, size } of OUTPUTS) {
  const svg = await readFile(resolve(root, source))
  const png = await sharp(svg, { density: 384 }).resize(size, size).png({ compressionLevel: 9 }).toBuffer()
  await writeFile(resolve(root, out), png)
  console.log(`${out}  ${size}x${size}  ${(png.length / 1024).toFixed(1)} kB`)
}

// The favicon is served from src/app/icon.svg by Next; the PNG above is a
// fallback for clients that will not take an SVG favicon.
await writeFile(resolve(root, 'src/app/icon.svg'), await readFile(resolve(root, 'assets/icon.svg')))
console.log('src/app/icon.svg  copied')
