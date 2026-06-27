import { withSentryConfig } from "@sentry/nextjs";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin();

// Conditional bundle analyzer — run with ANALYZE=true npm run build
const withBundleAnalyzer =
  process.env.ANALYZE === "true"
    ? require("@next/bundle-analyzer")({ enabled: true })
    : (config: any) => config;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone" as const,
  images: {
    remotePatterns: [
      { protocol: "https" as const, hostname: "**" },
      { protocol: "http" as const, hostname: "**" },
    ],
  },
  serverExternalPackages: [
    "@sentry/nextjs",
    "@opentelemetry/context-async-hooks",
  ],
  async rewrites() {
    return [
      {
        source: "/ingest/static/:path*",
        destination: "https://us-assets.i.posthog.com/static/:path*",
      },
      {
        source: "/ingest/:path*",
        destination: "https://us.i.posthog.com/:path*",
      },
      {
        source: "/ingest/decide",
        destination: "https://us.i.posthog.com/decide",
      },
    ];
  },
  skipTrailingSlashRedirect: true,
  async redirects() {
    return [
      // Redirect legacy /outcomes/* URLs to /workers/*
      {
        source: "/outcomes",
        destination: "/workers",
        permanent: true,
      },
      {
        source: "/outcomes/:id",
        destination: "/workers/:id",
        permanent: true,
      },
      {
        source: "/outcomes/:id/runs/:runId",
        destination: "/workers/:id/runs/:runId",
        permanent: true,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          // Clickjacking is handled by CSP frame-ancestors below (modern standard).
          // X-Frame-Options is intentionally omitted — it can't express wildcard subdomain
          // allowlists and would block the settings/brand iframe preview on store.crossnode.sh.
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              // Next.js requires unsafe-inline for its runtime scripts; unsafe-eval for hot reload (dev only, stripped in prod by Next.js)
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.youtube.com https://*.youtube.com https://s.ytimg.com https://*.google.com https://*.googleapis.com https://*.gstatic.com https://*.crisp.chat https://js.stripe.com https://crossnode.sh https://*.crossnode.sh https://challenges.cloudflare.com",
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://*.googleapis.com https://*.gstatic.com https://*.crisp.chat",
              // Allow same-origin + data URIs + blob + all schemes/hosts for logos
              "img-src 'self' data: blob: http: https: *",
              "font-src 'self' data: https://fonts.gstatic.com https://*.gstatic.com https://*.googleapis.com https://*.crisp.chat",
              // Allow YouTube, Google Docs, Stripe, Cloudflare Turnstile embeds + Crossnode Storefronts.
              // https: is intentionally broad — the audit page embeds arbitrary target websites in an iframe.
              "frame-src 'self' https: http: https://www.youtube.com https://youtube.com https://www.youtube-nocookie.com https://*.youtube.com https://docs.crossnode.sh https://store.crossnode.sh https://crossnode.sh https://*.crossnode.sh https://*.google.com https://*.googleapis.com https://*.gstatic.com https://js.stripe.com https://challenges.cloudflare.com",
              // PostHog, Sentry, Crisp, Stripe, Pexels, and Crossnode APIs
              // In dev, also allow direct calls to the local backend (agency audit page)
              `connect-src 'self' wss: https://www.youtube.com https://*.youtube.com https://docs.crossnode.sh https://store.crossnode.sh https://crossnode.sh https://*.crossnode.sh https://*.google.com https://*.googleapis.com https://*.gstatic.com https://*.crisp.chat wss://*.crisp.chat https://api.stripe.com https://us.i.posthog.com https://app.posthog.com https://api.pexels.com${process.env.NODE_ENV !== "production" ? " http://localhost:8000 http://127.0.0.1:8000" : ""}`,
              "frame-ancestors 'self' https://crossnode.sh https://*.crossnode.sh",
              "object-src 'none'",
              "base-uri 'self'",
            ].join("; "),
          },
          // Other hardening
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "on" },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
          {
            // Allow mic + camera on our OWN origin (self) — the voice cloner
            // records audio via getUserMedia, which a blanket microphone=()
            // blocks at the browser-policy level (throws NotAllowedError even
            // when the OS/browser permission is granted). geolocation stays off.
            key: "Permissions-Policy",
            value: "camera=(self), microphone=(self), geolocation=()",
          },
        ],
      },
    ];
  },
};

// Sentry Configuration Options
const sentryOptions = {
  org: "islam-xd4",
  project: "syntra-nextjs",

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // Upload a larger set of source maps for prettier stack traces (increases build time)
  widenClientFileUpload: true,

  // Route browser requests to Sentry through a Next.js rewrite to circumvent ad-blockers.
  tunnelRoute: "/monitoring",

  // NEW: Correctly nested options for source maps.
  sourcemaps: {
    deleteSourcemapsAfterUpload: true,
  },

  // IMPORTANT: Set this to FALSE while debugging.
  // If set to true, it removes the code capable of printing debug info to the console.
  disableLogger: false,

  // Enables automatic instrumentation of Vercel Cron Monitors.
  automaticVercelMonitors: true,
};

// Wrap the config ONCE (bundle analyzer -> intl -> sentry)
export default withBundleAnalyzer(
  withSentryConfig(withNextIntl(nextConfig), sentryOptions),
);
