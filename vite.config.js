import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local dev, /api/* is forwarded to the FastAPI server (uvicorn on port 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
