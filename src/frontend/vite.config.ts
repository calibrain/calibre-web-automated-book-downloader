import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    strictPort: true,
    cors: true,
    proxy: {
      // Proxy API requests to the Docker backend
      '/api': {
        target: 'http://localhost:8084',
        changeOrigin: true,
        secure: false,
      },
      // Note: Socket.IO connects directly to backend (port 8084) in dev mode
      // to avoid Vite WebSocket proxy issues. No proxy needed here.
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
