// File: web/src/components/landing/LandingHeader.tsx
//
// The landing header, alive. At the top of the page it sits wide and quiet;
// after a few pixels of scroll it gains a floating glass background (border +
// backdrop blur — no shadows, per DESIGN.md). Links carry an animated
// underline; the CTAs are always present. Entrance staggers with cn-enter so
// the first paint feels composed, not static. On small screens the nav
// collapses behind a menu toggle.
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Menu, X } from "lucide-react";

import Logo from "@/components/global/logo";
import { SALES_CALL_URL } from "@/lib/config";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/manifesto", label: "Manifesto" },
  { href: "/pricing", label: "Pricing" },
  { href: "/live", label: "Customers" },
  { href: "/blog", label: "Blog" },
];

export function LandingHeader({ ctaHref }: { ctaHref: string }) {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className="sticky top-0 z-30 w-full px-3 pt-3 sm:px-6">
      <div
        className={cn(
          // Stays WIDE + large on scroll (Kortix-style) — it only gains a glass
          // background + border, it does NOT shrink into a small pill.
          "mx-auto flex w-full max-w-7xl items-center justify-between rounded-full border px-4 py-3.5 transition-all duration-300 ease-out sm:px-6",
          scrolled || menuOpen
            ? "border-border bg-background/80 backdrop-blur-xl"
            : "border-transparent bg-transparent",
        )}
      >
        <div className="cn-enter">
          <Logo />
        </div>

        <nav className="hidden items-center gap-7 text-[13px] text-muted-foreground md:flex">
          {LINKS.map((l, i) => (
            <Link
              key={l.href}
              href={l.href}
              className="cn-enter group relative py-1 transition-colors hover:text-foreground"
              style={{ animationDelay: `${60 + i * 50}ms` }}
            >
              {l.label}
              {/* animated underline — scales in from the left on hover */}
              <span
                className="absolute inset-x-0 -bottom-0.5 h-px origin-left scale-x-0 bg-primary transition-transform duration-200 ease-out group-hover:scale-x-100"
                aria-hidden
              />
            </Link>
          ))}
        </nav>

        <div
          className="cn-enter flex items-center gap-2"
          style={{ animationDelay: "210ms" }}
        >
          <Link
            href="/login"
            className="cn-hover hidden px-3 py-1.5 text-[13px] text-muted-foreground hover:text-foreground sm:block"
          >
            Sign in
          </Link>
          {/* Primary header action: book a sales call (single source:
              SALES_CALL_URL). "Start free" is the hero CTA, so the header leads
              with Schedule a call instead of duplicating it. */}
          <a
            href={SALES_CALL_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="group inline-flex items-center gap-1.5 rounded-full bg-primary py-2 pl-4 pr-3 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Schedule a call
            <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
          </a>

          {/* Mobile menu toggle */}
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            className="cn-hover -mr-1 ml-1 rounded-full p-1.5 text-muted-foreground hover:text-foreground md:hidden"
          >
            {menuOpen ? (
              <X className="h-5 w-5" />
            ) : (
              <Menu className="h-5 w-5" />
            )}
          </button>
        </div>
      </div>

      {/* Mobile menu panel — borders + surface, no shadow (per DESIGN.md). */}
      {menuOpen && (
        <div className="cn-enter mx-auto mt-2 w-full max-w-7xl rounded-2xl border border-border bg-background/95 p-3 backdrop-blur-xl md:hidden">
          <nav className="flex flex-col">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                onClick={() => setMenuOpen(false)}
                className="cn-hover rounded-md px-3 py-2.5 text-[14px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
              >
                {l.label}
              </Link>
            ))}
          </nav>
          <div className="mt-2 flex flex-col gap-2 border-t border-border pt-3">
            <Link
              href="/login"
              onClick={() => setMenuOpen(false)}
              className="cn-hover rounded-md px-3 py-2.5 text-[14px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            >
              Sign in
            </Link>
            <a
              href={SALES_CALL_URL}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setMenuOpen(false)}
              className="group mt-1 inline-flex items-center justify-center gap-1.5 rounded-full bg-primary py-2.5 text-[14px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
              Schedule a call
              <ArrowRight className="h-4 w-4 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
