// File: web/src/components/global/GlassShaderLazy.tsx
//
// Client-only, lazily-loaded wrapper around the WebGL GlassShader. The shader
// canvas must never run on the server (no WebGL) or block first paint, so we
// `dynamic(..., { ssr: false })` it and ship a matching static CSS bloom as the
// loading/placeholder so the hero never flashes empty. Server components import
// THIS, not GlassShader directly.
"use client";

import dynamic from "next/dynamic";

// The static bloom rendered until the canvas mounts (and the permanent state for
// no-WebGL / SSR). On-palette ciel→indigo, edge-masked — pure CSS, no motion.
function StaticBloom({
  className = "",
  dark = false,
}: {
  className?: string;
  dark?: boolean;
}) {
  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute inset-0 z-0 overflow-hidden ${className}`}
      style={{
        background: dark
          ? "radial-gradient(120% 90% at 50% 0%, #16223f 0%, #0a1020 45%, #060810 100%)"
          : "radial-gradient(110% 80% at 38% 18%, oklch(0.78 0.10 240 / 0.30) 0%, oklch(0.85 0.06 244 / 0.12) 38%, transparent 70%)",
      }}
    />
  );
}

const GlassShaderClient = dynamic(
  () => import("./GlassShader").then((m) => m.GlassShader),
  {
    ssr: false,
    loading: () => <StaticBloom />,
  },
);

export function GlassShaderLazy(props: { className?: string; dark?: boolean }) {
  return <GlassShaderClient {...props} />;
}

export default GlassShaderLazy;
