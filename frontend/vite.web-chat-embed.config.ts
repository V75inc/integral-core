import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Separate opt-in artifact only. This does not add a route to Integral.
export default defineConfig({
  base: '/web-chat-embed-assets/',
  plugins: [react()],
  publicDir: false,
  build: {
    outDir: 'dist-web-chat-embed',
    rolldownOptions: { input: 'public-web-chat-embed.html' },
  },
});
