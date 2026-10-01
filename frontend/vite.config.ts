import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        // React is shared by every route and changes less often than product code.
        manualChunks(id) {
          if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) {
            return "react-runtime";
          }
        },
      },
    },
  },
});
