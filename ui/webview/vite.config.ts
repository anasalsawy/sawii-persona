import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vite";

// Output lands in ui/dist; the build script copies it into the Python package
// (src/sawii_persona/web/companion/) so the backend serves it at /app.
export default defineConfig({
	plugins: [react(), tailwindcss()],
	resolve: { alias: { "@": path.resolve(__dirname, "./src") }, dedupe: ["react", "react-dom"] },
	base: "./",
	build: { outDir: "../dist", emptyOutDir: true, cssMinify: "esbuild", chunkSizeWarningLimit: 900 },
});
