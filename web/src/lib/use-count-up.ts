"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Animated count-up to a target value. Eases out (the sanctioned ease-out
 * curve) over `duration` ms. Honors prefers-reduced-motion by jumping
 * straight to the target. This is the JS half of the score-in moment.
 */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setValue(target);
      return;
    }

    const start = performance.now();
    const from = 0;
    // cubic-bezier(0.16, 1, 0.3, 1) approximated as easeOutExpo.
    const ease = (t: number) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

    const tick = (now: number) => {
      const p = Math.min((now - start) / duration, 1);
      setValue(Math.round(from + (target - from) * ease(p)));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [target, duration]);

  return value;
}
