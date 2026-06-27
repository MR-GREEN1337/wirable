import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import Credentials from "next-auth/providers/credentials";

declare module "next-auth" {
  interface Session {
    backendToken?: string;
    userName?: string;
    isGuest?: boolean;
  }
}

// next-auth v5 beta doesn't expose a resolvable "next-auth/jwt" subpath for
// module augmentation, so we type the extra JWT fields locally instead.
type AppToken = {
  email?: string | null;
  name?: string | null;
  sub?: string;
  backendToken?: string;
  userName?: string;
  isGuest?: boolean;
};

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),

    // Guest login — receives a pre-issued backend JWT from the sign-in page
    Credentials({
      id: "guest",
      name: "Guest",
      credentials: {
        token: { label: "Token", type: "text" },
        name:  { label: "Name",  type: "text" },
      },
      async authorize(credentials) {
        if (!credentials?.token || !credentials?.name) return null;
        // Returned shape becomes `user` in the jwt callback. next-auth's User
        // type wants string fields, so we cast our extra props through unknown.
        return {
          id: `guest-${Date.now()}`,
          name: credentials.name as string,
          backendToken: credentials.token as string,
          isGuest: true,
        } as unknown as import("next-auth").User;
      },
    }),
  ],

  pages: {
    signIn: "/signin",
  },

  callbacks: {
    async jwt({ token: rawToken, account, user }) {
      const token = rawToken as AppToken;
      // Google OAuth path — exchange for backend JWT
      if (account?.provider === "google" && token.email) {
        try {
          const res = await fetch(
            `${process.env.BACKEND_URL}/api/v1/auth/google`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                email: token.email,
                name:  token.name,
                google_id: token.sub,
              }),
            }
          );
          if (res.ok) {
            const data = await res.json();
            token.backendToken = data.access_token as string;
            token.userName     = data.user?.name ?? token.name ?? "";
            token.isGuest      = false;
          }
        } catch { /* API down — UI works, API calls will 401 */ }
      }

      // Guest credentials path — backendToken is on the user object
      if (account?.provider === "guest" && (user as any)?.backendToken) {
        token.backendToken = (user as any).backendToken as string;
        token.userName     = user.name ?? "";
        token.isGuest      = true;
      }

      return token;
    },

    async session({ session, token: rawToken }) {
      const token = rawToken as AppToken;
      session.backendToken = token.backendToken;
      session.userName     = token.userName;
      session.isGuest      = token.isGuest;
      return session;
    },
  },
});
