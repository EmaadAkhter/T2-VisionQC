import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Served by the relay at https://<host>/admin/ (single-host deployment).
  base: '/admin/',
  plugins: [react()],
})
