import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";
import { Wordmark } from "@/components/global/Logo";
import { AccessChip } from "@/components/AccessGate";

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
        style={{
          background: "color-mix(in oklch, var(--surface-1) 85%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-6">
          <Wordmark href="/dashboard" size={20} />

          <div className="flex flex-1 items-center gap-6 text-[13px]">
            <a href="/dashboard" className="cn-hover" style={{ color: "var(--foreground)" }}>
              Dashboard
            </a>
          </div>

          <div className="flex items-center gap-3">
            {/* Entitlement: remaining free runs / unlimited / get-access */}
            <AccessChip />

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
