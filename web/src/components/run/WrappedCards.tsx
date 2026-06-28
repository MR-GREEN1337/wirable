"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { dimensionConcept } from "@/lib/run-icons";
import type { WrappedCardData } from "@/lib/run-events";

// ─────────────────────────────────────────────────────────────────────────────
// The verdict deck — a hand of cards fanned out and overlapping, each with a
// dense 1-bit DITHERED texture filling its top half (the "Wrapped" look), in the
// app's ciel-bleu theme. Click a card to read the whole thing in a dialog.
//
// The texture is a real ordered (Bayer 4×4) dither of a procedural cloudy field,
// painted as 1px ciel squares on a tiny canvas and scaled up with
// image-rendering:pixelated — so it reads like a dithered photo, not a dot grid.
// ─────────────────────────────────────────────────────────────────────────────

const ACCENT = "#4da6ff"; // ciel-bleu — the app's one accent, themes the deck

// Tone only inks the headline (meaning stays legible); the texture stays ciel.
function toneInk(card: WrappedCardData): string {
  if (card.dimension === "general") return "var(--foreground)";
  switch (card.tone) {
    case "good":
      return "#36c08a";
    case "bad":
      return "#ff6b5e";
    case "warn":
      return "#f0b429";
    default:
      return "var(--foreground)";
  }
}

// Deterministic tilt per index (no Math.random → SSR-safe). Reads as hand-fanned.
const TILT = [-5, 4, -3, 6, -4.5, 3.5, -6, 5, -2.5, 4.5, -4, 3];
const tiltFor = (i: number) => TILT[i % TILT.length];

// ── The dithered ciel texture ─────────────────────────────────────────────────
function DitherCanvas({ seed }: { seed: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const W = 104;
    const H = 64;
    cv.width = W;
    cv.height = H;

    let s = (seed * 2654435761 + 12345) >>> 0;
    const rnd = () => {
      s ^= s << 13;
      s >>>= 0;
      s ^= s >> 17;
      s ^= s << 5;
      s >>>= 0;
      return s / 4294967296;
    };

    const GX = 11;
    const GY = 8;
    const grid: number[] = [];
    for (let i = 0; i < GX * GY; i++) grid.push(rnd());
    const sample = (x: number, y: number) => {
      const fx = (x / (W - 1)) * (GX - 1);
      const fy = (y / (H - 1)) * (GY - 1);
      const x0 = Math.floor(fx);
      const y0 = Math.floor(fy);
      const x1 = Math.min(x0 + 1, GX - 1);
      const y1 = Math.min(y0 + 1, GY - 1);
      const tx = fx - x0;
      const ty = fy - y0;
      const a = grid[y0 * GX + x0];
      const b = grid[y0 * GX + x1];
      const c = grid[y1 * GX + x0];
      const d = grid[y1 * GX + x1];
      const top = a + (b - a) * tx;
      const bot = c + (d - c) * tx;
      return top + (bot - top) * ty;
    };

    const bayer = [
      [0, 8, 2, 10],
      [12, 4, 14, 6],
      [3, 11, 1, 9],
      [15, 7, 13, 5],
    ];

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = ACCENT;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const grad = Math.pow(y / (H - 1), 0.85);
        const v = grad * 0.82 + sample(x, y) * 0.6 - 0.16;
        const th = (bayer[y % 4][x % 4] + 0.5) / 16;
        if (v > th) ctx.fillRect(x, y, 1, 1);
      }
    }
  }, [seed]);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className="absolute inset-0 h-full w-full"
      style={{ imageRendering: "pixelated" }}
    />
  );
}

function cardSeed(card: WrappedCardData, index: number) {
  return index * 97 + (card.headline?.length ?? 0) * 13 + 7;
}

function WrappedCard({
  card,
  index,
  onOpen,
}: {
  card: WrappedCardData;
  index: number;
  onOpen: () => void;
}) {
  const tilt = tiltFor(index);

  return (
    <li
      className="wrapped-card group"
      style={
        {
          "--tilt": `${tilt}deg`,
          animation: `cn-enter 220ms cubic-bezier(0.16,1,0.3,1) ${Math.min(index * 55, 380)}ms both`,
        } as React.CSSProperties
      }
    >
      <button
        type="button"
        onClick={onOpen}
        className="wrapped-card__inner"
        aria-label={`Read: ${card.headline}`}
      >
        {/* top half: the dithered ciel texture + ports */}
        <div className="relative h-[124px] shrink-0 overflow-hidden" style={{ background: "var(--surface-2)" }}>
          <DitherCanvas seed={cardSeed(card, index)} />
          <div aria-hidden className="absolute left-3.5 top-3 flex gap-1.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--background)" }} />
            ))}
          </div>
        </div>

        {/* body: eyebrow question, headline, detail */}
        <div className="flex flex-1 flex-col gap-1.5 px-4 pb-4 pt-3 text-left">
          <span className="text-[11px] leading-tight" style={{ color: ACCENT }} title={card.eyebrow}>
            {card.eyebrow}
          </span>
          <h3
            className="font-display text-[19px] font-bold leading-[1.08] tracking-tight"
            style={{ color: toneInk(card) }}
          >
            {card.headline}
          </h3>
          <p
            className="text-[12px] leading-snug"
            style={{
              color: "var(--muted-foreground)",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {card.detail}
          </p>
        </div>
      </button>
    </li>
  );
}

// ── Read-the-whole-thing dialog ───────────────────────────────────────────────
function CardDialog({ card, index, onClose }: { card: WrappedCardData; index: number; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const concept = dimensionConcept(card.dimension);
  const Icon = concept.icon;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center p-4"
      style={{ background: "oklch(0 0 0 / 0.6)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="relative w-full max-w-md overflow-hidden rounded-xl border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative h-[160px] overflow-hidden" style={{ background: "var(--surface-2)" }}>
          <DitherCanvas seed={cardSeed(card, index)} />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="cn-hover absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-md"
            style={{ background: "color-mix(in oklch, var(--background) 70%, transparent)", color: "var(--foreground)" }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-3 p-6">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4" strokeWidth={2} style={{ color: ACCENT }} />
            <span className="text-[12px]" style={{ color: ACCENT }}>
              {card.eyebrow}
            </span>
          </div>
          <h3 className="font-display text-[28px] font-bold leading-[1.05] tracking-tight" style={{ color: toneInk(card) }}>
            {card.headline}
          </h3>
          <p className="text-[14px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
            {card.detail}
          </p>
          {card.dimension && card.dimension !== "general" && (
            <span
              className="mt-1 inline-flex w-fit items-center rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-[0.08em]"
              style={{ border: "1px solid var(--border)", color: "var(--muted-foreground)" }}
            >
              {card.dimension.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export function WrappedCards({ cards }: { cards: WrappedCardData[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!cards.length) return null;
  return (
    <>
      <style>{deckStyles}</style>
      <ul className="wrapped-deck">
        {cards.map((card, i) => (
          <WrappedCard key={`${card.dimension}-${i}`} card={card} index={i} onOpen={() => setOpen(i)} />
        ))}
      </ul>
      {open !== null && cards[open] && (
        <CardDialog card={cards[open]} index={open} onClose={() => setOpen(null)} />
      )}
    </>
  );
}

const deckStyles = `
.wrapped-deck {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: flex-start;
  gap: 0;
  padding: 28px 8px 16px;
}
.wrapped-card {
  flex: 0 0 196px;
  width: 196px;
  margin-left: -26px;
  margin-bottom: 8px;
  transform: rotate(var(--tilt));
  transform-origin: center bottom;
  transition: transform 200ms cubic-bezier(0.16,1,0.3,1);
  z-index: 1;
  will-change: transform;
}
.wrapped-card:first-child { margin-left: 0; }
.wrapped-card:hover { transform: rotate(0deg) scale(1.07) translateY(-8px); z-index: 30; }

.wrapped-card__inner {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 286px;
  overflow: hidden;
  border-radius: 8px;
  background: var(--surface-1);
  border: 1px solid color-mix(in oklch, ${ACCENT} 28%, var(--border));
  box-shadow: 0 1px 2px oklch(0 0 0 / 0.18);
  transition: box-shadow 200ms ease, border-color 200ms ease;
  cursor: pointer;
}
.wrapped-card:hover .wrapped-card__inner {
  border-color: color-mix(in oklch, ${ACCENT} 55%, transparent);
  box-shadow: 0 2px 6px oklch(0 0 0 / 0.22), 0 22px 48px oklch(0 0 0 / 0.34);
}

@media (max-width: 520px) {
  .wrapped-card { margin-left: 0; flex-basis: 100%; width: 100%; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .wrapped-deck { gap: 14px; }
  .wrapped-card { margin-left: 0; transform: none; transition: none; }
  .wrapped-card:hover { transform: none; }
}
`;
