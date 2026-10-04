import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Data folders change while recording / re-running models: never reload the page for them.
  server: {
    fs: { allow: [".."] },
    watch: { ignored: ["**/public/recordings/**", "**/public/out/**", "**/public/geo/**", "**/recordings/**", "**/out/**", "**/*.tsbuildinfo"] },
  },
  build: { chunkSizeWarningLimit: 2500 },
});
