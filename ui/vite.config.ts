import { defineConfig } from "vite";

// `npm run dev` serves the viewer with hot reload and forwards API/WebSocket calls to
// a running `genesis view <world> --no-browser` on port 8800.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8800",
      "/ws": { target: "ws://127.0.0.1:8800", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
});
