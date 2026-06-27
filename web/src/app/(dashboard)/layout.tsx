import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  if (!session) redirect("/signin");

  const isGuest   = (session as any).isGuest as boolean | undefined;
  const userName  = (session as any).userName as string | undefined
                    ?? session.user?.name
                    ?? null;
  const userEmail = session.user?.email;
  const avatar    = session.user?.image;

  // Display: "Cosmic Badger" for guests, email for real users
  const displayLabel = isGuest
    ? userName
    : (userEmail ?? userName ?? "—");

  return (
    <div
      className="min-h-screen"
      style={{ background: "var(--background)", color: "var(--foreground)" }}
    >
      <nav
        className="sticky top-0 z-40 border-b"
        style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
      >
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-6 px-6">
          <a href="/dashboard" className="font-display text-sm font-bold uppercase tracking-wider">
            AgentReady
          </a>

          <div className="flex flex-1 items-center gap-6 text-xs">
            <a href="/dashboard" className="cn-hover" style={{ color: "var(--muted-foreground)" }}>
              Dashboard
            </a>
            <a href="/console" className="cn-hover" style={{ color: "var(--muted-foreground)" }}>
              Scout
            </a>
          </div>

          <div className="flex items-center gap-3">
            {/* Guest badge or avatar */}
            {isGuest ? (
              <span
                className="inline-flex h-7 items-center gap-1.5 rounded border px-2 text-[11px] font-medium"
                style={{
                  borderColor: "oklch(0.65 0.16 240 / 0.4)",
                  background:  "oklch(0.65 0.16 240 / 0.08)",
                  color:       "oklch(0.65 0.16 240)",
                }}
              >
                guest
              </span>
            ) : avatar ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={avatar} alt={userName ?? ""} className="h-7 w-7 rounded-full" />
            ) : (
              <span
                className="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold uppercase"
                style={{ background: "var(--surface-3)", color: "var(--muted-foreground)" }}
              >
                {(displayLabel ?? "?")[0]}
              </span>
            )}

            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
              {displayLabel}
            </span>

            <a
              href="/api/auth/signout"
              className="cn-hover text-xs"
              style={{ color: "var(--muted-foreground)" }}
            >
              {isGuest ? "Exit" : "Sign out"}
            </a>
          </div>
        </div>
      </nav>

      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  );
}
