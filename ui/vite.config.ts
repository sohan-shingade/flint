import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
  },
  server: {
    port: Number(process.env.FLINT_UI_PORT ?? 5173),
    proxy: {
      '/api': `http://localhost:${process.env.FLINT_API_PORT ?? 8000}`,
      '/ws': { target: `ws://localhost:${process.env.FLINT_API_PORT ?? 8000}`, ws: true },
    },
  },
})
