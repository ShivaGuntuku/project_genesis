import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to a running `genesis dashboard <world> --no-browser`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8810" } },
  build: { chunkSizeWarningLimit: 1500 },
});
