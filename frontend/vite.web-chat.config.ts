import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Opt-in deployable asset only. The standard build does not include the
// visitor page, and this configuration does not register any app route.
export default defineConfig({
  base: '/web-chat-assets/',
  plugins: [react()],
  publicDir: false,
  build: {
    outDir: 'dist-web-chat',
    rolldownOptions: { input: 'public-web-chat.html' },
  },
});
