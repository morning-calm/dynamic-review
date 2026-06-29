import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The backend binds to 127.0.0.1:8000 only. In dev we proxy the API + audio
// streams through Vite so the browser talks to a single origin (port 5173).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/audio': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/overlays': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
