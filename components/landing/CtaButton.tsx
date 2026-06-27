"use client";

// CtaButton — the hero call-to-action. Compact + dark (Kortix-style), not a big
// rounded pill: a glassy dark button with a soft ciel glow behind it and a slow
// light sheen sweeping across the surface. Pure CSS, self-contained keyframes.

import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function CtaButton({
  href,
  children = "Start free",
}: {
  href: string;
  children?: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="group relative inline-flex items-center gap-2 overflow-hidden rounded-xl bg-foreground px-5 py-2.5 text-sm font-medium text-background transition-transform duration-200 ease-out hover:-translate-y-px"
    >
      {/* soft ciel glow behind the button (intensifies on hover) */}
      <span
        aria-hidden
        className="pointer-events-none absolute -inset-1.5 -z-10 rounded-2xl bg-primary/45 opacity-45 blur-lg transition-opacity duration-300 group-hover:opacity-75"
      />
      {/* glass top sheen */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-xl bg-gradient-to-b from-white/18 to-transparent"
      />
      {/* light sheen sweeping across (the glassy shine) */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-xl"
      >
        <span className="absolute -left-1/2 top-0 h-full w-1/3 -skew-x-[20deg] bg-white/25 blur-[3px] [animation:cta-sheen_3.4s_ease-in-out_infinite]" />
      </span>
      <span className="relative z-10 inline-flex items-center gap-2">
        {children}
        <ArrowRight className="h-4 w-4 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
      </span>
      <style>{`@keyframes cta-sheen{0%{transform:translateX(0)}55%,100%{transform:translateX(520%)}}`}</style>
    </Link>
  );
}
