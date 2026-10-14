import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

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
