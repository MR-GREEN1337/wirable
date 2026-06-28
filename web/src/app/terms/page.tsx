import Link from "next/link";
import { Wordmark } from "@/components/global/Logo";

export const metadata = {
  title: "Terms · Wirable",
  description: "Terms of Service and Privacy for Wirable.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="font-display text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>
        {title}
      </h2>
      <div className="text-[14px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
        {children}
      </div>
    </section>
  );
}

export default function TermsPage() {
  return (
    <div style={{ background: "var(--background)", color: "var(--foreground)", minHeight: "100vh" }}>
      <nav
        className="sticky top-0 z-40 border-b"
        style={{
          background: "color-mix(in oklch, var(--surface-1) 82%, transparent)",
          backdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-12 max-w-[760px] items-center px-6">
          <Wordmark href="/" size={22} />
        </div>
      </nav>

      <main className="mx-auto max-w-[760px] px-6 py-12">
        <p className="eyebrow mb-2" style={{ color: "var(--muted-foreground)" }}>
          legal
        </p>
        <h1 className="font-display text-[28px] font-semibold tracking-tight">Terms &amp; Privacy</h1>
        <p className="mt-2 text-[13px]" style={{ color: "var(--fg-subtle)" }}>
          Last updated June 2026. Wirable is in early launch; these terms are intentionally short and plain.
        </p>

        <div className="mt-10 flex flex-col gap-8">
          <Section title="What Wirable does">
            Wirable tests whether an AI agent can use a website or API you point it at, scores the result
            from 0 to 100, and can host an MCP proxy that fixes the gaps. You give us a URL; our agents
            drive it in an isolated sandbox and report what they find.
          </Section>

          <Section title="Acceptable use">
            Only submit URLs for products you own or are authorized to test. Do not use Wirable to attack,
            overload, scrape at abusive scale, or circumvent the security of systems you do not control.
            We run an isolated browser against the target; you are responsible for having the right to do so.
          </Section>

          <Section title="Credentials you provide">
            If you give the agent test credentials or an API key to reach an authenticated product, they are
            injected into the ephemeral sandbox for that single run and into the hosted proxy server-side so
            agents never see them. Use throwaway or scoped test credentials. Do not submit credentials you
            are not permitted to share.
          </Section>

          <Section title="Billing">
            Paid plans are billed through Stripe on a recurring basis until cancelled. You can cancel anytime;
            access continues through the end of the paid period. Prices are shown before checkout.
          </Section>

          <Section title="Privacy &amp; data">
            We store your account, the targets you test, the resulting scores, and proxy configuration to
            operate the service. We use Sentry for error monitoring and PostHog for product analytics, with
            personal data minimized. We do not sell your data. Audited page content is processed to produce
            the score and is not published by us.
          </Section>

          <Section title="No warranty">
            Wirable is provided &quot;as is&quot; during launch. The score is an automated assessment, not a
            security audit or a guarantee. Hosted proxies are best-effort. Do not rely on Wirable as your sole
            control for anything safety- or compliance-critical.
          </Section>

          <Section title="Contact">
            Questions: reach out via the Product Hunt page or the founder. We will update these terms as the
            product matures.
          </Section>
        </div>

        <div className="mt-12 border-t pt-6" style={{ borderColor: "var(--border)" }}>
          <Link href="/" className="text-[13px] cn-hover" style={{ color: "var(--primary)" }}>
            ← Back to Wirable
          </Link>
        </div>
      </main>
    </div>
  );
}
