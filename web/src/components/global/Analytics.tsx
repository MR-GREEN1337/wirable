"use client";

import { useEffect } from "react";

/**
 * Analytics — Sentry (errors) + PostHog (product analytics), both fully behind
 * env vars. When the env vars are unset, nothing is loaded and every helper is a
 * silent no-op. Packages are loaded via dynamic import() inside the effect so the
 * build/typecheck never depends on their types being installed.
 *
 * Env vars:
 *   NEXT_PUBLIC_SENTRY_DSN     — enables Sentry when set
 *   NEXT_PUBLIC_SENTRY_ENV     — Sentry environment (default "production")
 *   NEXT_PUBLIC_POSTHOG_KEY    — enables PostHog when set
 *   NEXT_PUBLIC_POSTHOG_HOST   — PostHog host (default https://us.i.posthog.com)
 */

// Lazily-resolved module handles. Stay null when keys/packages are missing, so
// the exported helpers below are safe no-ops in every environment.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let posthogClient: any = null;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let sentryClient: any = null;

/** Fire a product-analytics event. No-op when PostHog isn't loaded. */
export function track(event: string, props?: Record<string, unknown>): void {
  try {
    if (posthogClient) posthogClient.capture(event, props);
  } catch {
    // never let analytics throw into product code
  }
}

/** Report an error to Sentry. No-op when Sentry isn't loaded. */
export function captureError(e: unknown): void {
  try {
    if (sentryClient) sentryClient.captureException(e);
  } catch {
    // swallow — observability must never break the app
  }
}

export function Analytics() {
  useEffect(() => {
    let cancelled = false;

    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    if (dsn) {
      (async () => {
        try {
          // @ts-ignore — types resolve at build time (package installed in Docker); local tsc lacks them
          const Sentry = await import("@sentry/browser");
          if (cancelled) return;
          Sentry.init({
            dsn,
            environment: process.env.NEXT_PUBLIC_SENTRY_ENV || "production",
            tracesSampleRate: 0.1,
          });
          sentryClient = Sentry;
        } catch {
          // missing package / init failure → stay a no-op
        }
      })();
    }

    const phKey = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    if (phKey) {
      (async () => {
        try {
          // @ts-ignore — types resolve at build time (package installed in Docker); local tsc lacks them
          const mod = await import("posthog-js");
          if (cancelled) return;
          const posthog = mod.default ?? mod;
          posthog.init(phKey, {
            api_host:
              process.env.NEXT_PUBLIC_POSTHOG_HOST ||
              "https://us.i.posthog.com",
            capture_pageview: true,
            person_profiles: "identified_only",
          });
          posthogClient = posthog;
        } catch {
          // missing package / init failure → stay a no-op
        }
      })();
    }

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}

export default Analytics;
