import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `vite build --mode demo`: the server-less demo site (GitHub Pages) — recorded answers from demo-public/,
// relative paths so it works under any sub-path (https://<user>.github.io/<repo>/)
export default defineConfig(({ mode }) => {
  const demo = mode === "demo";
  return {
    plugins: [react()],
    base: demo ? "./" : "/",
    publicDir: demo ? "demo-public" : "public",
    server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
    build: { outDir: demo ? "dist-demo" : "dist", chunkSizeWarningLimit: 2000 },
  };
});
