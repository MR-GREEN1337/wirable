// File: web/src/components/landing/Reveal.tsx
"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Scroll-reveal wrapper. Plays the sanctioned `cn-enter` motion
 * (opacity 0→1, translateY 4px→0, 200ms ease-out) once when the element
 * enters the viewport. Honors prefers-reduced-motion by showing instantly.
 *
 * `delay` lets sibling items stagger in. Keep stagger small (≤120ms total).
 */
export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setShown(true);
      return;
    }

    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: shown ? undefined : 0,
        animation: shown
          ? `cn-enter 200ms cubic-bezier(0.16, 1, 0.3, 1) ${delay}ms both`
          : undefined,
      }}
    >
      {children}
    </div>
  );
}
