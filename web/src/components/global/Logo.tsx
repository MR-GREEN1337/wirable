/**
 * Wirable logomark — a single wire traced into a "W", with connection nodes
 * at each vertex (the ports an agent wires into). The center peak node is the
 * one ciel-bleu accent: the agent's live connection point. The wire uses
 * currentColor so the mark inherits the surrounding text color (works on light
 * + dark, in nav, footer, terminals). Brutally minimal — operator-grade.
 */

type LogoProps = {
  size?: number;
  className?: string;
  /** Override the accent node color (defaults to the ciel-bleu token). */
  accent?: string;
  title?: string;
};

export function Logo({ size = 24, className, accent = "var(--primary)", title = "Wirable" }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      role="img"
      aria-label={title}
      className={className}
    >
      {/* the wire — a W traced as one continuous patch cable */}
      <path
        d="M7 10 L15 30 L20 19 L25 30 L33 10"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      />
      {/* valley nodes — outlined ports */}
      <circle cx="15" cy="30" r="2.4" fill="var(--background)" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="25" cy="30" r="2.4" fill="var(--background)" stroke="currentColor" strokeWidth="1.8" />
      {/* top terminals */}
      <circle cx="7" cy="10" r="2.4" fill="var(--background)" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="33" cy="10" r="2.4" fill="var(--background)" stroke="currentColor" strokeWidth="1.8" />
      {/* center peak — the live agent connection, the one accent */}
      <circle cx="20" cy="19" r="3.4" fill={accent} />
      <circle cx="20" cy="19" r="6.2" fill="none" stroke={accent} strokeWidth="1.2" opacity="0.35" />
    </svg>
  );
}

/** Mark + wordmark, the standard lockup used in nav / footer. */
export function Wordmark({
  size = 22,
  className,
  href = "/",
}: {
  size?: number;
  className?: string;
  href?: string | null;
}) {
  const inner = (
    <span className="inline-flex items-center gap-2">
      <Logo size={size} />
      <span
        className="font-display font-bold uppercase tracking-[0.08em]"
        style={{ fontSize: size * 0.62, color: "var(--foreground)" }}
      >
        Wirable
      </span>
    </span>
  );
  if (href === null) return <span className={className}>{inner}</span>;
  return (
    <a href={href} className={className} aria-label="Wirable home">
      {inner}
    </a>
  );
}
