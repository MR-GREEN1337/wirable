"use client";

// DiffView — a clean unified-diff renderer for the agent-ready changes a fix PR
// contains. Parses `git diff` output itself (no deps): per-file collapsible
// sections with line numbers, add lines tinted green / removed tinted rose /
// context muted, and styled hunk headers. Lyra tokens, tabular nums.

import { useMemo, useState } from "react";
import { ChevronRight, FilePlus2, FileText } from "lucide-react";

type LineKind = "add" | "del" | "context" | "meta";

type DiffLine = {
  kind: LineKind;
  text: string;
  // line numbers in the old/new file (null where not applicable)
  oldNo: number | null;
  newNo: number | null;
};

type DiffFile = {
  path: string;
  isNew: boolean;
  isDelete: boolean;
  added: number;
  removed: number;
  lines: DiffLine[];
};

// ── parser ───────────────────────────────────────────────────────────────────
// Splits a unified diff into per-file blocks and tags each line with its kind +
// running old/new line numbers (derived from the @@ hunk headers).
function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  const lines = diff.replace(/\r\n/g, "\n").split("\n");
  let cur: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;

  const finish = () => {
    if (cur) files.push(cur);
  };

  for (const raw of lines) {
    if (raw.startsWith("diff --git")) {
      finish();
      // "diff --git a/path b/path" — prefer the b/ path (post-change).
      const m = raw.match(/ b\/(.+)$/);
      const path = m ? m[1] : raw.replace(/^diff --git\s+/, "");
      cur = { path, isNew: false, isDelete: false, added: 0, removed: 0, lines: [] };
      oldNo = 0;
      newNo = 0;
      continue;
    }
    if (!cur) continue;

    if (raw.startsWith("new file")) {
      cur.isNew = true;
      continue;
    }
    if (raw.startsWith("deleted file")) {
      cur.isDelete = true;
      continue;
    }
    // Skip noisy file-header metadata lines.
    if (
      raw.startsWith("index ") ||
      raw.startsWith("--- ") ||
      raw.startsWith("+++ ") ||
      raw.startsWith("old mode") ||
      raw.startsWith("new mode") ||
      raw.startsWith("similarity ") ||
      raw.startsWith("rename ")
    ) {
      continue;
    }

    if (raw.startsWith("@@")) {
      // @@ -oldStart,oldLen +newStart,newLen @@ optional section
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) {
        oldNo = parseInt(m[1], 10);
        newNo = parseInt(m[2], 10);
      }
      cur.lines.push({ kind: "meta", text: raw, oldNo: null, newNo: null });
      continue;
    }

    if (raw.startsWith("+")) {
      cur.added += 1;
      cur.lines.push({ kind: "add", text: raw.slice(1), oldNo: null, newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      cur.removed += 1;
      cur.lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++, newNo: null });
    } else if (raw.startsWith("\\")) {
      // "\ No newline at end of file" — keep as context, no numbering.
      cur.lines.push({ kind: "context", text: raw, oldNo: null, newNo: null });
    } else {
      cur.lines.push({
        kind: "context",
        text: raw.startsWith(" ") ? raw.slice(1) : raw,
        oldNo: oldNo++,
        newNo: newNo++,
      });
    }
  }
  finish();
  // Drop empty trailing artifacts (a stray block with no real lines).
  return files.filter((f) => f.lines.length > 0);
}

const KIND_BG: Record<LineKind, string> = {
  add: "color-mix(in oklch, var(--success) 12%, transparent)",
  del: "color-mix(in oklch, var(--danger) 12%, transparent)",
  context: "transparent",
  meta: "var(--surface-2)",
};

const KIND_GUTTER: Record<LineKind, string> = {
  add: "color-mix(in oklch, var(--success) 22%, transparent)",
  del: "color-mix(in oklch, var(--danger) 22%, transparent)",
  context: "transparent",
  meta: "var(--surface-2)",
};

function signFor(kind: LineKind): string {
  if (kind === "add") return "+";
  if (kind === "del") return "-";
  return " ";
}

function FileBlock({ file, defaultOpen }: { file: DiffFile; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const Icon = file.isNew ? FilePlus2 : FileText;

  return (
    <div
      className="overflow-hidden rounded-md border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="cn-hover flex w-full items-center gap-2 px-3 py-2 text-left"
        style={{ background: "var(--surface-2)" }}
        aria-expanded={open}
      >
        <ChevronRight
          className="h-3.5 w-3.5 shrink-0 transition-transform duration-[120ms]"
          style={{
            transform: open ? "rotate(90deg)" : "none",
            color: "var(--muted-foreground)",
          }}
          strokeWidth={2}
        />
        <Icon
          className="h-3.5 w-3.5 shrink-0"
          style={{ color: file.isNew ? "var(--success)" : "var(--muted-foreground)" }}
          strokeWidth={1.75}
        />
        <span
          className="data min-w-0 flex-1 truncate text-[12.5px] font-medium"
          style={{ color: "var(--foreground)" }}
          title={file.path}
        >
          {file.path}
        </span>
        {file.isNew && (
          <span
            className="rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
            style={{
              background: "color-mix(in oklch, var(--success) 15%, transparent)",
              color: "var(--success)",
            }}
          >
            new
          </span>
        )}
        <span className="data flex shrink-0 items-center gap-1.5 text-[11px]">
          {file.added > 0 && <span style={{ color: "var(--success)" }}>+{file.added}</span>}
          {file.removed > 0 && <span style={{ color: "var(--danger)" }}>−{file.removed}</span>}
        </span>
      </button>

      {open && (
        <div className="overflow-x-auto">
          <table
            className="data w-full border-collapse text-[12px]"
            style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}
          >
            <tbody>
              {file.lines.map((ln, i) => {
                if (ln.kind === "meta") {
                  return (
                    <tr key={i}>
                      <td
                        colSpan={3}
                        className="select-none px-3 py-1 text-[11px]"
                        style={{
                          background: KIND_BG.meta,
                          color: "var(--muted-foreground)",
                          borderTop: "1px solid var(--border)",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        {ln.text}
                      </td>
                    </tr>
                  );
                }
                return (
                  <tr key={i} style={{ background: KIND_BG[ln.kind] }}>
                    <td
                      className="select-none px-2 text-right align-top text-[11px] tabular-nums"
                      style={{
                        width: 44,
                        minWidth: 44,
                        color: "var(--fg-subtle)",
                        background: KIND_GUTTER[ln.kind],
                      }}
                    >
                      {ln.oldNo ?? ""}
                    </td>
                    <td
                      className="select-none px-2 text-right align-top text-[11px] tabular-nums"
                      style={{
                        width: 44,
                        minWidth: 44,
                        color: "var(--fg-subtle)",
                        background: KIND_GUTTER[ln.kind],
                      }}
                    >
                      {ln.newNo ?? ""}
                    </td>
                    <td
                      className="whitespace-pre px-3 align-top"
                      style={{
                        color:
                          ln.kind === "add"
                            ? "var(--success)"
                            : ln.kind === "del"
                              ? "var(--danger)"
                              : "var(--foreground)",
                      }}
                    >
                      <span
                        className="select-none"
                        style={{ color: "var(--fg-subtle)" }}
                      >
                        {signFor(ln.kind)}
                      </span>
                      {ln.text || " "}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  if (!files.length) return null;

  const totals = files.reduce(
    (acc, f) => ({ added: acc.added + f.added, removed: acc.removed + f.removed }),
    { added: 0, removed: 0 },
  );
  const truncated = /diff truncated by Wirable/.test(diff);

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <span className="eyebrow text-[10px]">changes in this PR</span>
        <span className="data flex items-center gap-2 text-[11px]">
          <span style={{ color: "var(--muted-foreground)" }}>
            {files.length} file{files.length === 1 ? "" : "s"}
          </span>
          {totals.added > 0 && <span style={{ color: "var(--success)" }}>+{totals.added}</span>}
          {totals.removed > 0 && <span style={{ color: "var(--danger)" }}>−{totals.removed}</span>}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {files.map((f, i) => (
          <FileBlock key={f.path + i} file={f} defaultOpen={files.length <= 2} />
        ))}
      </div>

      {truncated && (
        <p className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
          Diff truncated for display. Open the PR for the complete diff.
        </p>
      )}
    </div>
  );
}
