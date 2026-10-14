import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";

// A fingerprint of the exported data, added to every data URL (?v=...), so a redeploy with new data is never
// hidden behind Cloudflare's edge cache of the old files.
const dataFiles = ["public/data/index.json", "public/data/meta.json"].filter((f) => existsSync(f));
const DATA_VERSION = createHash("sha256").update(dataFiles.map((f) => readFileSync(f)).join("")).digest("hex").slice(0, 10);

/** Preload the Latin subset of the one text face every page opens with, so headlines don't wait for the stylesheet. */
function preloadFonts(): Plugin {
  return {
    name: "preload-fonts",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const fonts = Object.keys(ctx.bundle ?? {}).filter((f) => /assets\/schibsted-grotesk-latin-wght-normal-[\w-]+\.woff2$/.test(f));
        return fonts.map((f) => ({ tag: "link", attrs: { rel: "preload", as: "font", type: "font/woff2", href: `/${f}`, crossorigin: "" }, injectTo: "head" as const }));
      },
    },
  };
}

export default defineConfig({
  define: { __DATA_VERSION__: JSON.stringify(DATA_VERSION) },
  plugins: [react(), tailwindcss(), preloadFonts()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // The framework in its own files, so a returning visitor keeps them cached across redeploys.
        manualChunks: (id) => /node_modules\/(react|react-dom|scheduler)\//.test(id) ? "react"
          : /node_modules\/(motion|motion-dom|motion-utils|framer-motion)\//.test(id) ? "motion"
          : /node_modules\/react-router\//.test(id) ? "router" : undefined,
      },
    },
  },
  // `pnpm dev` serves the pages; /api/ask needs the worker, so run `pnpm build && pnpm preview` (wrangler dev) to try it.
  server: { proxy: { "/api": process.env.KILDE_API ?? "http://127.0.0.1:8787" } },
});
