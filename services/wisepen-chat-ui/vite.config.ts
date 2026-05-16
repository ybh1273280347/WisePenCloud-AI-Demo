import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const host = env.VITE_DEV_SERVER_HOST || "0.0.0.0";
  const port = Number(env.VITE_DEV_SERVER_PORT || env.PORT || 5173);
  const proxyTarget =
    env.VITE_CHAT_PROXY_TARGET ||
    env.VITE_CHAT_API_BASE_URL ||
    "http://localhost:9200";
  const proxyHeaders = {
    ...(env.VITE_FROM_SOURCE_SECRET
      ? { "X-From-Source": env.VITE_FROM_SOURCE_SECRET }
      : {}),
  };
  const chatProxy = {
    "/chat": {
      target: proxyTarget,
      changeOrigin: true,
      secure: false,
      headers: proxyHeaders,
    },
    "/api": {
      target: proxyTarget,
      changeOrigin: true,
      secure: false,
      headers: proxyHeaders,
    },
  };

  return {
    plugins: [react()],
    server: {
      host,
      port,
      strictPort: true,
      proxy: chatProxy,
    },
    preview: {
      host,
      port,
      strictPort: true,
      proxy: chatProxy,
    },
  };
});
