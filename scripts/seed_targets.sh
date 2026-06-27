#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Wirable — hardened demo targets
# ──────────────────────────────────────────────────────────────────────────────
# Two known-good, publicly-available targets for the live 90-second demo. One
# exposes a clean, documented API (the "agent-ready" MCP path). The other is a
# normal storefront with NO API (the agent must drive it via Playwright). Running
# both in a demo shows the full spread: from "already structured" to "had to
# operate the UI like a human".
#
# These are intentionally STABLE, public, and CORS/robots-friendly. They are not
# rate-limited honeypots and they do not require auth — safe to hit repeatedly
# during rehearsal.
#
# Usage:
#   scripts/seed_targets.sh              # print the documented target list
#   scripts/seed_targets.sh run         # fire BOTH targets through the local stack
#   scripts/seed_targets.sh run api     # fire only the API target
#   scripts/seed_targets.sh run noapi   # fire only the no-API target
#
# Requires the local stack to be up first:  scripts/demo.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── The two demo targets ──────────────────────────────────────────────────────
# Pick ONE per slot. If a target ever flakes on demo day, swap to its alternate.

# Slot 1 — clean API / structured surface (the "MCP / agent-ready" path).
#   Primary: Vercel's reference commerce demo store. It's a real Next.js
#            storefront backed by a documented Shopify Storefront GraphQL API,
#            so an agent can discover and call structured endpoints.
#   Alt:     https://dummyjson.com/products  (pure JSON REST, never sleeps)
TARGET_API_PRIMARY="https://demo.vercel.store"
TARGET_API_ALT="https://dummyjson.com"

# Slot 2 — NO public API (the "Playwright / operate-the-UI" path).
#   Primary: Sauce Labs' demo e-commerce site — a classic test target with a
#            login + cart flow and NO API. The agent must click through the DOM.
#   Alt:     http://books.toscrape.com  (static catalogue, no API, scrape-only)
TARGET_NOAPI_PRIMARY="https://www.saucedemo.com"
TARGET_NOAPI_ALT="http://books.toscrape.com"

API_URL="http://localhost:8000"
WEB_URL="http://localhost:3000"

log()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

print_targets() {
    cat <<EOF

Wirable — hardened demo targets
═══════════════════════════════════════════════════════════════════════════════

  1) API / structured (clean MCP path)
       Primary : ${TARGET_API_PRIMARY}
       Alt     : ${TARGET_API_ALT}        (pure JSON REST, always-on)
       Why     : real storefront backed by a documented GraphQL Storefront API —
                 the agent finds + calls structured endpoints, high score.

  2) No-API site (Playwright path)
       Primary : ${TARGET_NOAPI_PRIMARY}
       Alt     : ${TARGET_NOAPI_ALT}    (static catalogue, scrape-only)
       Why     : login + cart flow with NO API — the agent must operate the DOM
                 like a human, showing the browser-automation fallback.

  Run them through the local stack (stack must be up — scripts/demo.sh):

       scripts/seed_targets.sh run            # both, sequentially
       scripts/seed_targets.sh run api        # API target only
       scripts/seed_targets.sh run noapi      # no-API target only

  Or drive a single run directly:

       scripts/demo.sh test ${TARGET_API_PRIMARY}
       scripts/demo.sh test ${TARGET_NOAPI_PRIMARY}

═══════════════════════════════════════════════════════════════════════════════
EOF
}

fire() {
    local url="$1"
    command -v curl >/dev/null 2>&1 || die "curl is required to run targets."
    curl -fsS "${API_URL}/health" >/dev/null 2>&1 \
        || die "Backend not reachable at ${API_URL}. Run scripts/demo.sh first."
    log "Firing run against ${url} (streaming via scripts/demo.sh test)…"
    "${SCRIPT_DIR}/demo.sh" test "${url}"
}

case "${1:-}" in
    ""|list|show)
        print_targets
        ;;
    run)
        case "${2:-both}" in
            api)        fire "${TARGET_API_PRIMARY}" ;;
            noapi)      fire "${TARGET_NOAPI_PRIMARY}" ;;
            both|"")    fire "${TARGET_API_PRIMARY}"; echo; fire "${TARGET_NOAPI_PRIMARY}" ;;
            *)          die "Unknown run target '${2}'. Use: api | noapi | both" ;;
        esac
        ;;
    *)
        die "Unknown command '${1}'. Use: (no arg) | run [api|noapi|both]"
        ;;
esac
