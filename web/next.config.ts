import type { NextConfig } from "next";

// Same-origin API proxy: when the client uses relative URLs (NEXT_PUBLIC_BACKEND_URL=""),
// the Next server forwards /api/* to the backend over the internal network. Lets the
// whole app run behind a single externally-open port (no second port / CORS / cloud-fw).
const PROXY_TARGET = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // ONLY proxy the backend API (/api/v1/*). Must NOT catch /api/auth/* —
    // those are NextAuth's own routes (providers/callback/session); proxying
    // them to the backend 404s and breaks guest + Google sign-in.
    return [
      { source: "/api/v1/:path*", destination: `${PROXY_TARGET}/api/v1/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Permissions-Policy",
            value: "microphone=(self)",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
