import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Google's favicon service — a crisp 32px mark for any domain. */
export function faviconUrl(domain: string, size = 32): string {
  const clean = (domain || "")
    .trim()
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "");
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(
    clean
  )}&sz=${size}`;
}
