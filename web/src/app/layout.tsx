import type { Metadata } from "next";
// NOTE: DESIGN.md mandates Roobert (sans) + Roobert Mono (data) + Chakra Petch (display).
// Roobert / Roobert Mono are commercial fonts, not available on Google Fonts, so we use
// Inter + JetBrains Mono as the closest open substitutes (do not break the build chasing
// an unlicensed font). Chakra Petch is correct and loaded as the display/accent face.
import { Chakra_Petch, Inter, JetBrains_Mono } from "next/font/google";
import { SessionProvider } from "next-auth/react";
import { auth } from "@/lib/auth";
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
    default: "AgentReady",
    template: "%s — AgentReady",
  },
  description:
    "Audit your product for AI agent compatibility. Get a score, a fix PR, and verified proof — in minutes.",
  openGraph: {
    title: "AgentReady",
    description:
      "Audit your product for AI agent compatibility. Get a score, a fix PR, and verified proof — in minutes.",
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
        <SessionProvider session={session}>{children}</SessionProvider>
      </body>
    </html>
  );
}
