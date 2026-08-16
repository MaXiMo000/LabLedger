import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],

  resolve: {
    // react-three-fiber is its own React renderer. Without dedupe, Vite's
    // dependency pre-bundling can hand it a second copy of React, and the
    // renderer's hooks then run against a different dispatcher — surfacing as
    // "Invalid hook call / more than one copy of React" the moment the Canvas
    // mounts.
    dedupe: ["react", "react-dom", "three"],
  },

  optimizeDeps: {
    // Deliberately NOT listing react/react-dom: pre-bundling them produces a
    // second module instance whose dispatcher is separate from the one the
    // app renders with, which is what "Invalid hook call" reports.
    include: ["three", "@react-three/fiber"],
  },

  server: {
    // Vite otherwise picks 5173 and quietly increments when it is taken, which
    // breaks any tool that was told which port to expect. Honouring PORT lets
    // a second instance be started on an assigned port; unset, nothing changes.
    port: process.env.PORT ? Number(process.env.PORT) : undefined,

    // The API runs on 8000. Proxying keeps the browser on one origin, so the
    // refresh cookie stays same-site in development exactly as it will in
    // production behind a single domain.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
