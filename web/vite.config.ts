import { resolve } from "node:path";
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
  build: {
    chunkSizeWarningLimit: 2500,
    // the team's pages (build plan, heat offer / prices, proposal) ship next to the control room
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        team: resolve(__dirname, "team/index.html"),
        offer: resolve(__dirname, "team/offer.html"),
        proposal: resolve(__dirname, "team/proposal.html"),
      },
    },
  },
});
