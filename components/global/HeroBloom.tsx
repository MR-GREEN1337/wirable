// File: web/src/components/global/HeroBloom.tsx
//
// The Lyra signature: a single sky→indigo radial bloom (lifted from the
// DashboardHero). DESIGN.md allows it ONLY on hero / login / onboarding /
// empty-state surfaces — never on functional chrome. Render inside a
// `relative overflow-hidden` parent; it sits behind content (-z-0) and rises
// from the bottom-center.

const BLOOM_GRADIENT =
  "radial-gradient(ellipse at 50% 70%, rgba(56,189,248,0.55) 0%, " +
  "rgba(14,165,233,0.42) 18%, rgba(2,132,199,0.28) 36%, " +
  "rgba(99,102,241,0.14) 56%, rgba(186,230,253,0.06) 74%, transparent 90%)";

const NOISE_SVG =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' " +
  "width='512' height='512'%3E%3Cfilter id='n'%3E%3CfeTurbulence " +
  "type='fractalNoise' baseFrequency='0.68' numOctaves='4' " +
  "stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' " +
  "height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

export function HeroBloom({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute inset-0 -z-0 overflow-hidden ${className}`}
    >
      <div
        className="absolute bottom-[-18%] left-1/2 -translate-x-1/2"
        style={{
          width: 1100,
          height: 800,
          maskImage:
            "radial-gradient(50% 60% at 50% 100%, black 0%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(50% 60% at 50% 100%, black 0%, transparent 100%)",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: BLOOM_GRADIENT,
            filter: "blur(16px)",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: NOISE_SVG,
            backgroundSize: "200px 200px",
            opacity: 0.08,
            mixBlendMode: "overlay",
          }}
        />
      </div>
    </div>
  );
}
