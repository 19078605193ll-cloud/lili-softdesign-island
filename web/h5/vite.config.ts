import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  base: "/h5/",
  server: {
    proxy: {
      "/api": {
        target: process.env.H5_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (req) =>
            req.setHeader(
              "Origin",
              process.env.H5_PUBLIC_ORIGIN || "http://127.0.0.1:8000",
            ),
          );
        },
      },
    },
  },
});
