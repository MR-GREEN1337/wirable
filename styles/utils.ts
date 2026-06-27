import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import * as Color from "color-bits";
import { formatDistanceToNow } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const colorWithOpacity = (color: string, opacity: number): string => {
  if (!color.startsWith("rgb")) return color;
  return Color.formatRGBA(Color.alpha(Color.parse(color), opacity));
};

export const getRGBA = (
  cssColor: React.CSSProperties["color"],
  fallback: string = "rgba(180, 180, 180)",
): string => {
  if (typeof window === "undefined") return fallback;
  if (!cssColor) return fallback;

  try {
    if (typeof cssColor === "string" && cssColor.includes("var(")) {
      const element = document.createElement("div");
      element.style.color = cssColor;
      document.body.appendChild(element);
      const computedColor = window.getComputedStyle(element).color;
      document.body.removeChild(element);
      return Color.formatRGBA(Color.parse(computedColor));
    }
    return Color.formatRGBA(Color.parse(cssColor));
  } catch (e) {
    console.error("Color parsing failed:", e);
    return fallback;
  }
};

// New utility to normalize filenames for cross-platform consistency
export const normalizeFilenameToNFC = (filename: string): string => {
  try {
    let normalized = filename.normalize("NFC");
    const unicodeSpaces = [
      "\u00A0",
      "\u2000",
      "\u2001",
      "\u2002",
      "\u2003",
      "\u2004",
      "\u2005",
      "\u2006",
      "\u2007",
      "\u2008",
      "\u2009",
      "\u200A",
      "\u202F",
      "\u205F",
      "\u3000",
    ];
    for (const space of unicodeSpaces) {
      normalized = normalized.replaceAll(space, " ");
    }
    return normalized;
  } catch (error) {
    console.warn("Failed to normalize filename:", filename, error);
    return filename;
  }
};

export function formatRelativeTime(timestamp: string | Date): string {
  try {
    let dateVal = timestamp;

    // --- FIX: Force UTC interpretation for backend dates ---
    // Backend sends '2023-01-01T12:00:00'. We must treat this as UTC.
    // If we don't append 'Z', browser treats it as Local Time.
    if (
      typeof timestamp === "string" &&
      !timestamp.endsWith("Z") &&
      !timestamp.includes("+")
    ) {
      dateVal = timestamp + "Z";
    }

    const date = new Date(dateVal);
    const now = new Date();
    const secondsDiff = (now.getTime() - date.getTime()) / 1000;

    // If the event happened within the last 60 seconds
    if (secondsDiff >= 0 && secondsDiff < 60) {
      return "Just now";
    }

    return formatDistanceToNow(date, { addSuffix: true });
  } catch (error) {
    console.error("Invalid timestamp for formatRelativeTime:", timestamp);
    return "a while ago";
  }
}

/**
 * Extracts a Python docstring from a function definition.
 * @param code The Python code string.
 * @returns The cleaned docstring, or null if not found.
 */
export function extractDocstring(code: string): string | null {
  if (!code) {
    return null;
  }

  // Regex to match @tool decorator, function definition, and docstring
  // Matches: @tool (with optional parentheses and args), then def with args, then docstring
  const docstringRegex =
    /@tool(?:\([^)]*\))?\s*\n\s*def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:\s*(?:#.*?\n)*\s*("""([\s\S]*?)"""|'''([\s\S]*?)''')/;

  const match = code.match(docstringRegex);

  if (!match) {
    return null;
  }

  // The docstring content is in capture group 2 (for """) or 3 (for ''')
  const rawDocstring = match[2] || match[3];
  if (!rawDocstring) {
    return null;
  }

  // Clean up the docstring: remove leading/trailing whitespace and dedent.
  const lines = rawDocstring.split("\n");

  // Find the indentation of the first non-empty line
  const firstLineIndex = lines.findIndex((line) => line.trim() !== "");
  if (firstLineIndex === -1) {
    return rawDocstring.trim(); // All whitespace
  }

  const firstLine = lines[firstLineIndex];
  const indentationMatch = firstLine.match(/^\s*/);
  const indentation = indentationMatch ? indentationMatch[0] : "";

  if (indentation) {
    const dedentedLines = lines.map((line) =>
      line.startsWith(indentation) ? line.substring(indentation.length) : line,
    );
    return dedentedLines.join("\n").trim();
  }

  return rawDocstring.trim();
}
