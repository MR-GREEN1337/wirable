// Shared helpers for the "equip the user with the best" MCP adoption surfaces:
// the client config JSON, the Cursor deep link, and a base64url encoder. Used by
// ProxyPanel (per-proxy) and the /registry directory.

export function mcpSlug(domain: string): string {
  return (
    (domain || "mcp")
      .replace(/^https?:\/\//, "")
      .replace(/\/.*$/, "")
      .replace(/[^a-z0-9]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase() || "mcp"
  );
}

// The standard MCP client config (Claude Desktop / Cursor / others all read this shape).
export function mcpConfigJson(mcpUrl: string, slug: string): string {
  return JSON.stringify(
    { mcpServers: { [slug]: { url: mcpUrl, transport: "http" } } },
    null,
    2,
  );
}

function b64url(s: string): string {
  const b =
    typeof btoa !== "undefined"
      ? btoa(s)
      : // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (globalThis as any).Buffer.from(s).toString("base64");
  return b.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Cursor one-click install deep link.
export function cursorDeepLink(mcpUrl: string, slug: string): string {
  const cfg = b64url(JSON.stringify({ url: mcpUrl, transport: "http" }));
  return `cursor://anysphere.cursor-deeplink/mcp/install?name=${encodeURIComponent(slug)}&config=${cfg}`;
}
