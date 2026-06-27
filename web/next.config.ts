import type { NextConfig } from "next";

// Same-origin API proxy: when the client uses relative URLs (NEXT_PUBLIC_BACKEND_URL=""),
// the Next server forwards /api/* to the backend over the internal network. Lets the
// whole app run behind a single externally-open port (no second port / CORS / cloud-fw).
const PROXY_TARGET = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${PROXY_TARGET}/api/:path*` },
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
