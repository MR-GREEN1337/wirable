// File: web/src/components/landing/OutboundWorkflow.tsx
//
// "Watch one deal go from a name to a booked call." Built to the Kortix bar:
// ONE persistent product window (tab chrome + panel + action) that progressively
// populates as you scroll, a floating terminal that types each step's command,
// and tall scroll-synced steps on the left (dark pill eyebrow + headline + spark
// bullets). Clickable tabs + clickable steps, scroll-driven active state.
//
// The story doubles as the product explanation AND the trust pitch: step 04
// (approve) and 05 (delivery) are the anti-spray differentiator; step 07 renders
// the trust receipt as an audit feed — our answer to Kortix's git-log.
"use client";

import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type ComponentType,
} from "react";
import {
  Check,
  Shield,
  ArrowRight,
  Asterisk,
  Calendar,
  Lock,
  Pencil,
  Repeat,
} from "lucide-react";

/* ----------------------------- console content --------------------------- */

function Avatar({ s }: { s: string }) {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-3 font-mono text-[10px] font-medium text-muted-foreground">
      {s}
    </span>
  );
}

function MetaBadge({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "primary" | "emerald";
}) {
  const tones = {
    muted: "bg-surface-2 text-muted-foreground",
    primary: "bg-primary-soft/50 text-primary",
    emerald: "bg-emerald-500/12 text-emerald-600",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

function ViewTarget() {
  const rows: [string, ReactNode][] = [
    ["Role", "Founder · VP Growth"],
    ["Industry", "B2B SaaS, seed to Series A"],
    ["Headcount", "10-50"],
    ["Geo", "United States"],
    [
      "Signals",
      <span key="s" className="flex flex-wrap justify-end gap-1">
        <MetaBadge tone="primary">hiring a rep</MetaBadge>
        <MetaBadge tone="primary">raised &lt; 90d</MetaBadge>
      </span>,
    ],
  ];
  return (
    <div className="flex flex-col gap-px overflow-hidden rounded-md border border-border bg-border">
      {rows.map(([k, v]) => (
        <div
          key={k}
          className="flex items-center justify-between gap-3 bg-background px-3.5 py-2.5"
        >
          <span className="text-[12px] text-muted-foreground">{k}</span>
          <span className="data text-right text-[12px] font-medium text-foreground">
            {v}
          </span>
        </div>
      ))}
    </div>
  );
}

function ViewProspects() {
  const rows = [
    ["DW", "Dana Whitfield", "Founder", "Northwind", "raised $3.2M"],
    ["ML", "Marcus Lee", "VP Growth", "Tideline", "hiring 2 SDRs"],
    ["PN", "Priya Nair", "Founder", "Cortexa", "new RevOps lead"],
    ["SO", "Sam Okafor", "CEO", "Brightline", "shipped v2"],
  ];
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <div
          key={r[1]}
          className="flex items-center gap-3 rounded-md border border-border bg-background px-3 py-2"
        >
          <Avatar s={r[0]} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[12px] font-medium text-foreground">
              {r[1]}
            </span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {r[2]} · {r[3]}
            </span>
          </span>
          <MetaBadge tone="primary">{r[4]}</MetaBadge>
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
        </div>
      ))}
    </div>
  );
}

function ViewDraft() {
  return (
    <div className="flex flex-col overflow-hidden rounded-md border border-border bg-background">
      <div className="flex flex-col gap-1 border-b border-border px-3.5 py-2.5 text-[12px]">
        <div className="flex justify-between">
          <span className="text-muted-foreground">
            To <span className="text-foreground">marcus@tideline.io</span>
          </span>
          <MetaBadge tone="primary">personalized</MetaBadge>
        </div>
        <span className="text-muted-foreground">
          Subj{" "}
          <span className="text-foreground">Tideline&rsquo;s 2 new SDRs</span>
        </span>
      </div>
      <div className="space-y-2 px-3.5 py-3 text-[12px] leading-relaxed">
        <p className="text-foreground">
          Hi Marcus,{" "}
          <span className="rounded bg-primary-soft/50 px-1 text-primary">
            saw you&rsquo;re hiring two SDRs
          </span>
          . Usually means pipeline&rsquo;s the bottleneck, not headcount.
        </p>
        <p className="text-muted-foreground">
          We book qualified calls for seed SaaS founders without the spray.
          Every message is reviewed by a human first. Worth 15 min?
        </p>
      </div>
      <div className="flex items-center justify-between border-t border-border px-3.5 py-2 font-mono text-[11px] text-muted-foreground">
        <span>draft 1 / 146</span>
        <span className="text-primary">one real reason each</span>
      </div>
    </div>
  );
}

function ViewApprovals() {
  const rows = [
    ["Marcus Lee", "Tideline’s 2 new SDRs…"],
    ["Dana Whitfield", "Congrats on the $3.2M…"],
    ["Priya Nair", "Saw your new RevOps lead…"],
  ];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary-soft/20 px-3 py-2">
        <Lock className="h-3.5 w-3.5 text-primary" />
        <span className="text-[12px] font-medium text-foreground">
          12 messages waiting for you
        </span>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          held
        </span>
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <div
            key={r[0]}
            className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2"
          >
            <span className="min-w-0 flex-1">
              <span className="block text-[12px] font-medium text-foreground">
                {r[0]}
              </span>
              <span className="block truncate text-[11px] text-muted-foreground">
                {r[1]}
              </span>
            </span>
            <Check className="h-4 w-4 shrink-0 text-emerald-600" />
            <Pencil className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3.5 py-1.5 text-[12px] font-medium text-primary-foreground">
          <Check className="h-3.5 w-3.5" /> Approve all
        </span>
        <span className="rounded-full border border-border px-3.5 py-1.5 text-[12px] text-muted-foreground">
          Review each
        </span>
      </div>
    </div>
  );
}

function Gauge({ pct, label }: { pct: number; label: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        <span className="data text-[13px] font-semibold text-foreground">
          {pct}%
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-2">
        <span
          className="block h-full rounded-full bg-primary"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ViewDelivery() {
  const checks = [
    ["SPF · DKIM · DMARC", "aligned"],
    ["Sending domain", "warmed · 41 days"],
    ["Volume", "32 / inbox / day"],
  ];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-px overflow-hidden rounded-md border border-border bg-border">
        {checks.map(([k, v]) => (
          <div
            key={k}
            className="flex items-center justify-between gap-3 bg-background px-3.5 py-2"
          >
            <span className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <Check className="h-3.5 w-3.5 text-emerald-600" />
              {k}
            </span>
            <span className="data text-[12px] font-medium text-foreground">
              {v}
            </span>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Gauge pct={97} label="Inbox placement" />
        <Gauge pct={3} label="Spam rate · max 30" />
      </div>
    </div>
  );
}

function ViewInbox() {
  return (
    <div className="flex flex-col gap-2">
      {[
        ["ML", "Marcus Lee", "“Good timing. Thursday work?”", "interested"],
        ["DW", "Dana Whitfield", "“Send me the details.”", "interested"],
      ].map((r) => (
        <div
          key={r[1]}
          className="flex items-center gap-3 rounded-md border border-border bg-background px-3 py-2.5"
        >
          <Avatar s={r[0]} />
          <span className="min-w-0 flex-1">
            <span className="block text-[12px] font-medium text-foreground">
              {r[1]}
            </span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {r[2]}
            </span>
          </span>
          <MetaBadge tone="emerald">{r[3]}</MetaBadge>
        </div>
      ))}
      <div className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary-soft/20 px-3 py-2.5">
        <Calendar className="h-4 w-4 text-primary" />
        <span className="text-[12px] text-foreground">
          Meeting booked · Marcus Lee · Thu 2:00 PM
        </span>
      </div>
    </div>
  );
}

function ViewReport() {
  const stats = [
    ["146", "Sent"],
    ["21", "Replied"],
    ["7", "Booked"],
  ];
  const feed: [string, string, string][] = [
    ["sent 146 · approved by you", "Jun 14", "a1f3c2"],
    ["12 replies classified", "Jun 16", "9d04b7"],
    ["7 meetings booked", "Jun 19", "c7e912"],
    ["report signed · trust receipt", "Jul 01", "verify"],
  ];
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border border-border bg-border">
        {stats.map(([v, k]) => (
          <div key={k} className="bg-background px-2 py-3 text-center">
            <div className="data font-display text-xl font-semibold text-foreground">
              {v}
            </div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {k}
            </div>
          </div>
        ))}
      </div>
      <div className="overflow-hidden rounded-md border border-border bg-background">
        {feed.map(([msg, date, hash], i) => (
          <div
            key={hash}
            className={`flex items-center gap-2.5 px-3 py-1.5 ${i === feed.length - 1 ? "bg-primary-soft/15" : ""}`}
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${i === feed.length - 1 ? "bg-primary" : "border border-muted-foreground/50"}`}
            />
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
              {msg}
            </span>
            <span className="data shrink-0 font-mono text-[10px] text-muted-foreground/70">
              {date}
            </span>
            <span className="data shrink-0 font-mono text-[10px] text-primary">
              {hash}
            </span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
        <Shield className="h-3.5 w-3.5 text-primary" />
        verified · which model, which tools, who approved, when
      </div>
    </div>
  );
}

/* -------------------------------- steps ---------------------------------- */

type Step = {
  n: string;
  tab: string;
  badge: string;
  title: string;
  lead: string;
  bullets: string[];
  panelTitle: string;
  action: string;
  command: string;
  result: string;
  view: ComponentType;
};

const STEPS: Step[] = [
  {
    n: "01",
    tab: "Targeting",
    badge: "Target",
    title: "Name who you want as a customer",
    lead: "Describe the ideal client in plain language. No filters to wrangle, no list to buy.",
    bullets: [
      "Role, industry, size, geography, all in a sentence",
      "Buying signals that say they're in-market now",
      "The agent turns it into a precise, sourced query",
    ],
    panelTitle: "Ideal client profile",
    action: "Build list",
    command: 'crossnode outreach define --icp "seed b2b saas"',
    result: "✓ 2,410 companies match",
    view: ViewTarget,
  },
  {
    n: "02",
    tab: "Prospects",
    badge: "Build",
    title: "The agent builds the list",
    lead: "It sources the right companies, finds the right people, and enriches each across multiple data sources.",
    bullets: [
      "Waterfall enrichment: verified emails, not guesses",
      "A real reason each one is worth reaching today",
      "Deduped against everyone you've ever contacted",
    ],
    panelTitle: "Prospects · 146 sourced",
    action: "Enrich",
    command: "crossnode outreach build --enrich",
    result: "✓ 146 sourced · 78% verified",
    view: ViewProspects,
  },
  {
    n: "03",
    tab: "Drafts",
    badge: "Write",
    title: "One real email per person",
    lead: "Not merge-tag spam. One genuine, specific opener per prospect: the thing that makes a cold email feel hand-written.",
    bullets: [
      "A single true reason you're reaching out",
      "In your client's voice, on their offer",
      "Short, plain-text, the way real email looks",
    ],
    panelTitle: "Draft · Marcus Lee",
    action: "Regenerate",
    command: "crossnode outreach draft",
    result: "✓ 146 drafts · 1 reason each",
    view: ViewDraft,
  },
  {
    n: "04",
    tab: "Approvals",
    badge: "Approve",
    title: "You approve every send",
    lead: "The whole batch stops at your gate. Approve all, edit a line, or reject. Nothing leaves under your name until a human signs off.",
    bullets: [
      "Enforced by the platform, not the model's judgement",
      "Edit any message inline before it goes",
      "A named human on every send. That's the moat",
    ],
    panelTitle: "Approval queue",
    action: "Approve all",
    command: "crossnode outreach review",
    result: "▸ 12 awaiting your approval",
    view: ViewApprovals,
  },
  {
    n: "05",
    tab: "Delivery",
    badge: "Send",
    title: "It sends the right way",
    lead: "Warmed domains, aligned authentication, low daily volume, inbox-placement watched. The discipline that keeps you out of spam.",
    bullets: [
      "SPF · DKIM · DMARC aligned, domains warmed",
      "Low volume per inbox: personalized, never a blast",
      "Placement and spam-rate monitored in real time",
    ],
    panelTitle: "Deliverability",
    action: "Live",
    command: "crossnode outreach send",
    result: "✓ sent · 97% inbox placement",
    view: ViewDelivery,
  },
  {
    n: "06",
    tab: "Inbox",
    badge: "Reply",
    title: "Replies become booked calls",
    lead: "Every reply is read, classified, and qualified. Positive ones land on the calendar; the rest are handled.",
    bullets: [
      "Intent classified: interested, later, not now",
      "Qualified replies booked straight to your calendar",
      "You wake up to conversations, not an inbox",
    ],
    panelTitle: "Inbox · qualified",
    action: "Book",
    command: "crossnode outreach replies",
    result: "✓ 7 meetings booked",
    view: ViewInbox,
  },
  {
    n: "07",
    tab: "Report",
    badge: "Prove",
    title: "Monthly proof, with a receipt",
    lead: "Sent, replied, booked. The numbers that matter, in a branded report with a trust receipt the client can verify.",
    bullets: [
      "An auditable feed of every action the fleet took",
      "Which model, which tools, who approved it, when",
      "The proof that earns the renewal",
    ],
    panelTitle: "Campaign report · June",
    action: "Export",
    command: "crossnode outreach report --month",
    result: "✓ report ready · trust receipt",
    view: ViewReport,
  },
];

/* --------------------------- the product window --------------------------- */

function Console({ step }: { step: Step }) {
  // typewriter for the floating terminal command
  const [typed, setTyped] = useState("");
  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setTyped(step.command);
      return;
    }
    setTyped("");
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setTyped(step.command.slice(0, i));
      if (i >= step.command.length) clearInterval(id);
    }, 22);
    return () => clearInterval(id);
  }, [step.command]);

  const done = typed === step.command;
  const View = step.view;

  return (
    <div className="relative">
      {/* app window */}
      <div className="overflow-hidden rounded-xl border border-border bg-surface-1 shadow-[0_1px_2px_rgba(20,24,31,0.04),0_12px_32px_-12px_rgba(20,24,31,0.18)]">
        {/* title bar */}
        <div className="flex items-center gap-2 border-b border-border bg-surface-2/60 px-3.5 py-2.5">
          <span className="flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/25" />
            <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/25" />
            <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/25" />
          </span>
          <span className="ml-1 font-mono text-[11px] text-muted-foreground">
            crossnode · outreach
          </span>
          <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
            live
          </span>
        </div>

        {/* panel header */}
        <div className="flex items-center justify-between gap-3 px-4 pb-2 pt-3.5">
          <div className="min-w-0">
            <h3 className="truncate font-display text-[15px] font-semibold tracking-tight text-foreground">
              {step.panelTitle}
            </h3>
          </div>
          <span className="shrink-0 rounded-md bg-foreground px-2.5 py-1 text-[11px] font-medium text-background">
            {step.action}
          </span>
        </div>

        {/* swapping content */}
        <div className="px-4 pb-5 pt-1">
          <div key={step.n} className="cn-enter min-h-[14.5rem]">
            <View />
          </div>
        </div>
      </div>

      {/* floating terminal overlay */}
      <div className="absolute -bottom-5 -right-3 w-[19rem] overflow-hidden rounded-lg border border-border bg-[#0e1116] shadow-[0_16px_40px_-12px_rgba(20,24,31,0.45)]">
        <div className="flex items-center gap-2 border-b border-white/10 px-3 py-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          <span className="font-mono text-[10px] text-white/45">terminal</span>
        </div>
        <div className="px-3 py-2.5 font-mono text-[11px] leading-relaxed">
          <div className="flex">
            <span className="mr-1.5 shrink-0 text-emerald-400">$</span>
            <span className="break-all text-white/80">
              {typed}
              {!done && (
                <span className="ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 bg-white/70 align-middle" />
              )}
            </span>
          </div>
          {done && (
            <div className="cn-enter mt-1 text-white/45">{step.result}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------- the loop ------------------------------- */
// The dual purpose, stated plainly: the same engine WINS the agency its clients
// (self-outbound + lead magnets) and then SERVES every client it lands. The
// 7-step console below is that one engine; this band names what it's for.

function LoopCard({
  n,
  eyebrow,
  title,
  lead,
  chipLabel,
  chips,
}: {
  n: string;
  eyebrow: string;
  title: string;
  lead: string;
  chipLabel: string;
  chips: string[];
}) {
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface-1 p-5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-primary">{n}</span>
        <span className="eyebrow text-muted-foreground">{eyebrow}</span>
      </div>
      <h3 className="mt-2 font-display text-[19px] font-semibold leading-tight tracking-tight text-foreground">
        {title}
      </h3>
      <p className="mt-2 text-[14px] leading-relaxed text-muted-foreground">
        {lead}
      </p>
      <div className="mt-auto pt-4">
        <p className="text-[11px] text-muted-foreground/70">{chipLabel}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c}
              className="rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11px] text-muted-foreground"
            >
              {c}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function TheLoop() {
  return (
    <div className="mt-10 grid items-stretch gap-4 md:grid-cols-[1fr_auto_1fr]">
      <LoopCard
        n="01"
        eyebrow="For your agency"
        title="Win your own clients"
        lead="Crossnode runs outbound for YOU — and builds lead magnets that pull buyers in — so your agency fills its own pipeline with booked calls."
        chipLabel="Lead magnets that capture inbound"
        chips={["Calculator", "Scored quiz", "Benchmark", "Free audit"]}
      />

      {/* connector — the same engine, both directions */}
      <div className="flex items-center justify-center gap-2 md:flex-col">
        <span className="flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface-1 text-primary">
          <Repeat className="h-4 w-4" />
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          one engine
        </span>
      </div>

      <LoopCard
        n="02"
        eyebrow="For every client"
        title="Then serve everyone you sign"
        lead="Sign a client and the same fleet runs the exact playbook in their voice, books their calls, and ships the proof — all under their brand."
        chipLabel="Runs on autopilot, you approve every send"
        chips={["Their voice", "Their brand", "Their pipeline"]}
      />
    </div>
  );
}

/* ------------------------------- component -------------------------------- */

export function OutboundWorkflow() {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    // Active = the step whose vertical center is nearest the viewport center.
    const compute = () => {
      const els = refs.current.filter(Boolean) as HTMLLIElement[];
      if (!els.length) return;
      const center = window.innerHeight / 2;
      let best = 0;
      let bestDist = Infinity;
      els.forEach((el, i) => {
        const r = el.getBoundingClientRect();
        const dist = Math.abs(r.top + r.height / 2 - center);
        if (dist < bestDist) {
          bestDist = dist;
          best = i;
        }
      });
      setActive(best);
    };
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(compute);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    compute();
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const jump = (i: number) =>
    refs.current[i]?.scrollIntoView({ behavior: "smooth", block: "center" });

  return (
    <section className="border-t border-border">
      <div className="mx-auto w-full max-w-6xl px-6 pb-28 pt-28 sm:pb-36 sm:pt-36">
        <div className="max-w-2xl">
          <p className="eyebrow mb-5">The flywheel</p>
          <h2 className="font-display text-[clamp(1.75rem,4vw,2.5rem)] font-semibold leading-[1.1] tracking-tight">
            Win your agency its clients. Then run it for every client you land.
          </h2>
          <p className="mt-4 text-[15px] leading-relaxed text-muted-foreground sm:text-base">
            One engine does both. It earns you clients — cold outbound plus lead
            magnets that pull buyers in — then runs the same playbook for each
            client you sign, under their brand. You stay in exactly one loop:
            the approval gate.
          </p>
        </div>

        {/* the dual purpose, named */}
        <TheLoop />

        {/* the engine itself, step by step */}
        <div className="mt-20 max-w-2xl border-t border-border pt-16">
          <p className="eyebrow mb-3">Inside the engine</p>
          <h3 className="font-display text-[clamp(1.5rem,3.2vw,2rem)] font-semibold leading-[1.12] tracking-tight">
            Watch one deal go from a name to a booked call.
          </h3>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">
            The exact same seven steps run whether it&rsquo;s for your agency or
            a client. This is what they&rsquo;re buying.
          </p>
        </div>

        {/* tab strip — clickable, jumps to the step */}
        <div className="mt-8 flex flex-wrap gap-1.5">
          {STEPS.map((s, i) => (
            <button
              key={s.tab}
              type="button"
              onClick={() => jump(i)}
              aria-current={i === active}
              className={`cn-hover rounded-full border px-3 py-1.5 font-mono text-[11px] transition-colors duration-200 ${
                i === active
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-surface-1 text-muted-foreground hover:border-primary/40 hover:text-foreground"
              }`}
            >
              <span className="opacity-60">{s.n}</span> {s.tab}
            </button>
          ))}
        </div>

        <div className="mt-8 grid gap-12 lg:grid-cols-[1fr_minmax(0,32rem)] lg:gap-20">
          {/* Left: tall stepped blocks */}
          <ol>
            {STEPS.map((s, i) => {
              const on = i === active;
              return (
                <li
                  key={s.tab}
                  ref={(el) => {
                    refs.current[i] = el;
                  }}
                  className="flex min-h-[18rem] scroll-mt-28 flex-col justify-center py-8 lg:min-h-[72vh]"
                >
                  <button
                    type="button"
                    onClick={() => jump(i)}
                    className="block text-left"
                  >
                    <span
                      className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1 font-mono text-[11px] font-medium transition-colors duration-200 ${
                        on
                          ? "bg-foreground text-background"
                          : "bg-surface-2 text-muted-foreground"
                      }`}
                    >
                      {s.n} · {s.badge}
                    </span>
                    <h3
                      className={`mt-5 font-display text-[clamp(1.5rem,3vw,2rem)] font-semibold leading-[1.12] tracking-tight transition-colors duration-200 ${
                        on ? "text-foreground" : "text-muted-foreground"
                      }`}
                    >
                      {s.title}
                    </h3>
                  </button>
                  <p className="mt-4 max-w-md text-[15px] leading-relaxed text-muted-foreground">
                    {s.lead}
                  </p>
                  <ul className="mt-5 space-y-2.5">
                    {s.bullets.map((b) => (
                      <li
                        key={b}
                        className="flex items-start gap-2.5 text-sm leading-relaxed text-muted-foreground"
                      >
                        <Asterisk className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                        {b}
                      </li>
                    ))}
                  </ul>

                  {/* inline window on mobile */}
                  <div className="mt-8 lg:hidden">
                    <Console step={s} />
                  </div>
                </li>
              );
            })}
          </ol>

          {/* Right: sticky product window (desktop) */}
          <div className="hidden lg:block">
            <div className="sticky top-28">
              <Console step={STEPS[active]} />
            </div>
          </div>
        </div>

        {/* anti-spray closer */}
        <div className="mt-12 flex flex-col gap-4 border-t border-border pt-12 sm:flex-row sm:items-center sm:justify-between">
          <p className="max-w-xl text-sm leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">
              The whole point:
            </span>{" "}
            outbound that doesn&rsquo;t spam. Low volume, hand-checked, built to
            land in the inbox. The opposite of the autonomous blast tools that
            burned the channel.
          </p>
          <a
            href="/signup"
            className="group inline-flex shrink-0 items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Run your first campaign
            <ArrowRight className="h-4 w-4 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
          </a>
        </div>
      </div>
    </section>
  );
}
