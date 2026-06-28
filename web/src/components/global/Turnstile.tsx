"use client";

import { useEffect, useRef } from "react";

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js";

// Minimal typing for the global the Cloudflare script injects.
declare global {
  interface Window {
    turnstile?: {
      render: (
        el: HTMLElement,
        opts: {
          sitekey: string;
          callback: (token: string) => void;
          "expired-callback"?: () => void;
          "error-callback"?: () => void;
          theme?: "light" | "dark" | "auto";
        }
      ) => string;
      reset: (widgetId?: string) => void;
      remove: (widgetId?: string) => void;
    };
  }
}

let scriptPromise: Promise<void> | null = null;

/** Load the Turnstile script exactly once across the app. */
function loadScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${SCRIPT_SRC}"]`
    );
    if (existing) {
      // Already in the DOM (possibly still loading) — poll for the global.
      const wait = () => {
        if (window.turnstile) resolve();
        else setTimeout(wait, 50);
      };
      wait();
      return;
    }
    const s = document.createElement("script");
    s.src = SCRIPT_SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("turnstile script failed to load"));
    document.head.appendChild(s);
  });

  return scriptPromise;
}

/**
 * Cloudflare Turnstile widget.
 *
 * Contract:
 *   - props: { onToken: (token: string) => void }
 *   - calls onToken(token) on a successful challenge
 *   - calls onToken("") when the token expires or errors (so the caller can
 *     re-disable its submit button); the widget auto-resets on expiry
 *   - if NEXT_PUBLIC_TURNSTILE_SITE_KEY is missing, renders nothing and calls
 *     onToken("") once so dev still works (backend bypasses when its secret is
 *     empty)
 */
export function Turnstile({ onToken }: { onToken: (token: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  // Keep the latest callback without re-running the render effect.
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;

  useEffect(() => {
    if (!SITE_KEY) {
      // No site key in this environment — signal "no token needed" and bail.
      onTokenRef.current("");
      return;
    }

    let cancelled = false;

    loadScript()
      .then(() => {
        if (cancelled || !containerRef.current || !window.turnstile) return;
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: SITE_KEY,
          theme: "auto",
          callback: (token: string) => onTokenRef.current(token),
          "expired-callback": () => {
            onTokenRef.current("");
            if (widgetIdRef.current && window.turnstile) {
              window.turnstile.reset(widgetIdRef.current);
            }
          },
          "error-callback": () => onTokenRef.current(""),
        });
      })
      .catch(() => onTokenRef.current(""));

    return () => {
      cancelled = true;
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* widget already gone */
        }
        widgetIdRef.current = null;
      }
    };
  }, []);

  if (!SITE_KEY) return null;

  return (
    <div className="flex justify-center">
      <div ref={containerRef} style={{ width: 300, minHeight: 65 }} />
    </div>
  );
}

export default Turnstile;
