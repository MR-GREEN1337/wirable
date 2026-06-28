import type { Metadata } from "next";
// NOTE: DESIGN.md mandates Roobert (sans) + Roobert Mono (data) + Chakra Petch (display).
// Roobert / Roobert Mono are commercial fonts, not available on Google Fonts, so we use
// Inter + JetBrains Mono as the closest open substitutes (do not break the build chasing
// an unlicensed font). Chakra Petch is correct and loaded as the display/accent face.
import { Chakra_Petch, Inter, JetBrains_Mono } from "next/font/google";
import { SessionProvider } from "next-auth/react";
import { auth } from "@/lib/auth";
import { Analytics } from "@/components/global/Analytics";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const chakraPetch = Chakra_Petch({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-chakra",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Wirable",
    template: "%s · Wirable",
  },
  description:
    "Test whether an AI agent can complete real workflows on your platform, get a score, then host an MCP proxy that fixes the breakage.",
  openGraph: {
    title: "Wirable",
    description:
      "Test whether an AI agent can complete real workflows on your platform, get a score, then host an MCP proxy that fixes the breakage.",
    type: "website",
  },
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();

  return (
    <html
      lang="en"
      className={`${inter.variable} ${chakraPetch.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Analytics />
        <SessionProvider session={session}>{children}</SessionProvider>
      </body>
    </html>
  );
}
