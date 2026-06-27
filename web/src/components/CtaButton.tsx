"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface CtaButtonProps {
  href?: string;
  onClick?: () => void;
  children?: React.ReactNode;
  className?: string;
  type?: "button" | "submit" | "reset";
  disabled?: boolean;
  size?: "sm" | "md";
}

export function CtaButton({
  href,
  onClick,
  children = "Get started",
  className,
  type = "button",
  disabled,
  size = "md",
}: CtaButtonProps) {
  const inner = (
    <span className="inline-flex items-center gap-2">
      {children}
      <ArrowRight className={size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"} />
    </span>
  );

  // Solid ciel-bleu primary. Hover = color-only (slight lightness lift), 80ms.
  // No glow halo, no sheen, no translate — per DESIGN.md motion + no-shadow rules.
  const baseClasses = cn(
    "group inline-flex items-center gap-2 rounded-md font-medium",
    "bg-primary text-primary-foreground",
    "transition-colors duration-[80ms] ease-linear",
    "hover:bg-[oklch(0.69_0.16_240)]",
    "disabled:pointer-events-none disabled:opacity-50",
    size === "sm" ? "px-4 py-2 text-xs" : "px-5 py-2.5 text-sm",
    className
  );

  if (href) {
    return (
      <Link href={href} className={baseClasses}>
        {inner}
      </Link>
    );
  }

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={baseClasses}
    >
      {inner}
    </button>
  );
}
