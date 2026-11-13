import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the console runs on :5173 and proxies the API (including the
// SSE stream) to `catchbunny serve` on :8700. In production `catchbunny serve`
// serves web/dist itself, so there is one origin either way.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/v1": { target: "http://127.0.0.1:8700", changeOrigin: true } },
  },
  build: { chunkSizeWarningLimit: 1400 },
});
