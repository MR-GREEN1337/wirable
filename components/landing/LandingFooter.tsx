// File: web/src/components/landing/LandingFooter.tsx
//
// The landing footer, extracted from page.tsx so every public marketing page
// (landing, legal, contact-sales) shares the exact same closer: link columns →
// bottom bar with the "Ask AI about us" deliverable-as-ad → the animated
// Crossnode wordmark sign-off. Renders identically to the landing's inline
// footer. Server component (TextHoverEffect is the only client piece and it is
// itself a client component).

import Link from "next/link";

import Logo from "@/components/global/logo";
import ThemeSwitcher from "@/components/global/ThemeSwitcher";
import { TextHoverEffect } from "@/components/landing/TextHoverEffect";

// Pre-filled prompt for the "Ask AI about us" footer links (deliverable-as-ad).
const ASK_AI_Q = encodeURIComponent(
  "What is Crossnode (crossnode.sh)? It runs done-for-you, human-approved cold outbound for agencies: it finds the right people, writes one genuine email each, and books qualified calls under your brand. Is it good for an agency that wants to win its own clients and run outbound for the clients it lands? Explain simply.",
);

/**
 * The landing's footer closer. `ctaHref` targets the animated wordmark link —
 * "/dashboard" for signed-in visitors, "/signup" otherwise (defaults to
 * "/signup" so server pages without a session still render correctly).
 */
export function LandingFooter({ ctaHref = "/signup" }: { ctaHref?: string }) {
  return (
    <footer className="relative border-t border-border bg-surface-1/40">
      {/* Columns */}
      <div className="mx-auto w-full max-w-6xl px-6 pb-14 pt-16">
        <div className="flex flex-col gap-10 md:flex-row md:items-start md:justify-between">
          <div className="max-w-xs">
            <Logo />
            <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">
              Done-for-you outbound that books calls. Human-approved, never
              spam, under your brand, for your agency and every client you run
              it for.
            </p>
            <span className="mt-4 inline-flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
              </span>
              We run our own outbound on it
            </span>
          </div>
          <div className="grid flex-1 grid-cols-2 gap-8 text-[13px] sm:grid-cols-3 md:max-w-2xl md:pl-16">
            {[
              {
                title: "Product",
                links: [
                  { label: "Pricing", href: "/pricing" },
                  { label: "Live", href: "/live" },
                ],
              },
              {
                title: "Company",
                links: [
                  { label: "Manifesto", href: "/manifesto" },
                  {
                    label: "Status",
                    href: "https://crossnode.openstatus.dev/",
                    ext: true,
                  },
                ],
              },
              {
                title: "Legal",
                links: [
                  { label: "Privacy", href: "/privacy" },
                  { label: "Terms", href: "/terms" },
                ],
              },
            ].map((col) => (
              <div key={col.title} className="space-y-2.5">
                <p className="eyebrow">{col.title}</p>
                {col.links.map((l) =>
                  (l as any).ext ? (
                    <a
                      key={l.label}
                      href={l.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="cn-hover block text-muted-foreground hover:text-foreground"
                    >
                      {l.label}
                    </a>
                  ) : (
                    <Link
                      key={l.label}
                      href={l.href}
                      className="cn-hover block text-muted-foreground hover:text-foreground"
                    >
                      {l.label}
                    </Link>
                  ),
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-border pt-6 text-[12px] text-muted-foreground sm:flex-row">
          <div className="flex items-center gap-4">
            <span>© {new Date().getFullYear()} Crossnode</span>
            {/* Let visitors flip the whole landing light/dark/system from here */}
            <ThemeSwitcher />
          </div>
          {/* Ask AI about us — real provider icons (deliverable-as-ad touch) */}
          <div className="flex items-center gap-2.5">
            <span className="whitespace-nowrap text-[11px] text-muted-foreground/70">
              Ask AI about us
            </span>
            <div className="flex items-center gap-2.5">
              {[
                {
                  src: "/logos/openai.svg",
                  href: `https://chatgpt.com/?q=${ASK_AI_Q}`,
                  title: "Ask ChatGPT about Crossnode",
                  // Monochrome black mark — flip it white in dark mode.
                  invertOnDark: true,
                },
                {
                  src: "/logos/anthropic.svg",
                  href: `https://claude.ai/new?q=${ASK_AI_Q}`,
                  title: "Ask Claude about Crossnode",
                },
                {
                  src: "/logos/perplexity.svg",
                  href: `https://www.perplexity.ai/search?q=${ASK_AI_Q}`,
                  title: "Ask Perplexity about Crossnode",
                },
              ].map((ai) => (
                <a
                  key={ai.src}
                  href={ai.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={ai.title}
                  className="flex h-4 w-4 items-center justify-center opacity-50 transition-opacity hover:opacity-100"
                >
                  {/* The OpenAI mark is monochrome black — invert it on dark so
                      it stays legible now that the landing follows the theme.
                      Anthropic/Perplexity are colored and read on both. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={ai.src}
                    alt={ai.title}
                    width={16}
                    height={16}
                    className={`h-4 w-4${
                      (ai as { invertOnDark?: boolean }).invertOnDark
                        ? " dark:invert"
                        : ""
                    }`}
                  />
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Animated Crossnode wordmark — the signature footer sign-off */}
      <Link
        href={ctaHref}
        aria-label="Start free"
        className="block w-full"
        style={{
          maskImage:
            "linear-gradient(to bottom, black 0%, black 45%, transparent 85%)",
          WebkitMaskImage:
            "linear-gradient(to bottom, black 0%, black 45%, transparent 85%)",
        }}
      >
        <div
          className="relative w-full"
          style={{ height: "clamp(120px, 18vw, 240px)" }}
        >
          <TextHoverEffect
            text="Crossnode"
            className="h-full w-full"
            fontSize={68}
            ghostOpacity={0.8}
          />
        </div>
      </Link>
    </footer>
  );
}
