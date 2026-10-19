import type { NextConfig } from "next";

// The API is the Python backend (backend/). The browser only talks to this app, so the session cookie set by
// /api/auth/login is sent to both, and proxy.ts checks it before anything is forwarded.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  experimental: {
    // Tender uploads go through proxy.ts on their way to the backend, which accepts up to 50MB.
    proxyClientMaxBodySize: "51mb",
  },
};

export default nextConfig;
