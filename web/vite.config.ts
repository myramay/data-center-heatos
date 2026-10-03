import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const TILE_PROXY = {
  target: "https://a.basemaps.cartocdn.com",
  changeOrigin: true,
  rewrite: (p: string) => p.replace(/^\/tiles/, "/rastertiles/voyager"),
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Map tiles are proxied same-origin: CARTO does not send CORS headers WebGL textures need.
  server: { fs: { allow: [".."] }, proxy: { "/tiles": TILE_PROXY } },
  preview: { proxy: { "/tiles": TILE_PROXY } },
  build: { chunkSizeWarningLimit: 2500 },
});
