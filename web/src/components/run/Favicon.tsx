"use client";

import { useState } from "react";
import { Globe } from "lucide-react";
import { faviconUrl } from "@/lib/utils";

/** Domain favicon with a graceful Globe fallback when the mark fails to load. */
export function Favicon({
  domain,
  size = 16,
  className,
}: {
  domain: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (!domain || failed) {
    return (
      <Globe
        className={className}
        style={{ width: size, height: size, color: "var(--muted-foreground)" }}
        strokeWidth={1.75}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={faviconUrl(domain, size * 2)}
      alt=""
      width={size}
      height={size}
      className={className}
      style={{ width: size, height: size, borderRadius: 3, display: "block" }}
      onError={() => setFailed(true)}
    />
  );
}

/** Live pulse — a calm breathing dot. Only render while a run is in flight. */
export function LivePulse({
  color = "var(--primary)",
  size = 8,
}: {
  color?: string;
  size?: number;
}) {
  return (
    <span
      className="relative inline-flex shrink-0"
      style={{ width: size, height: size }}
    >
      <span
        className="absolute inset-0 rounded-full"
        style={{
          background: color,
          animation: "live-pulse 2s cubic-bezier(0.16,1,0.3,1) infinite",
        }}
      />
      <span
        className="relative inline-flex rounded-full"
        style={{ width: size, height: size, background: color }}
      />
    </span>
  );
}
