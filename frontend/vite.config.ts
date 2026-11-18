import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/studies": "http://127.0.0.1:8000",
      "/validations": "http://127.0.0.1:8000",
      "/sources": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/runs": "http://127.0.0.1:8000",
      "/comparisons": "http://127.0.0.1:8000",
      "/exports": "http://127.0.0.1:8000",
      "/schemas": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: false,
    testTimeout: 30000,
  },
});
