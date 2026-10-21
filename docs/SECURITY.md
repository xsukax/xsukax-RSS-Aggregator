# Security architecture and operational notes

**Pre-release, not independently security audited.** Do not expose the application directly to the internet without appropriate tunnel/access policies and operational hardening.

## Current protections

- First-run admin creation requires a high-entropy token saved in `.env` and is disabled after an administrator exists.
- Admin passwords are hashed with Argon2id; no plaintext passwords are persisted.
- Login sessions use random tokens; the database stores only a key-based HMAC digest. State-changing operations require a per-session CSRF token.
- Cookies are HttpOnly, SameSite Strict; `Secure` is set when PUBLIC_URL begins with `https://`.
- Docker publishes only `127.0.0.1` and drops Linux capabilities, runs as UID 10001, and uses a read-only root filesystem.
- Feed retrieval rejects non-HTTP schemes, URL-embedded credentials, most unusual ports, and non-public IP addresses including loopback/private/link-local ranges.
- DNS addresses are checked before establishing a TCP connection, and the HTTP(S) connector pins the destination IP while retaining the original hostname for TLS certificate verification. Every HTTP redirect gets revalidated.
- Feed response size, response timeout, compressed payload expansion, and redirect count are capped.
- XML parsing forbids DTDs, external entities, and entity expansion. Syndicated HTML is sanitized before persistent storage and rendering. Article scripts are not executed in the reader.
- SQL statements use parameter binding; FTS5 MATCH expressions are supplied as values. Dangerous filename handling is avoided because enclosures are exposed only as external links.
- The web app avoids wildcard CORS and uses CSP, frame denial, and other browser headers.

## Known limitations

- The UI permits publisher-hosted images to load directly in the browser. These requests can reveal browser IP and user-agent to the publisher; image blocking is not yet implemented.
- No audit has covered the entire application, all versions of dependencies, or the container image.
- The local HTTP endpoint is not TLS-protected. Bind it only to loopback and use an HTTPS tunnel for public access. Prefer Cloudflare Access to protect the hostname.
- The app is single-admin only; no organization access controls, two-factor authentication, or per-feed HTTP credentials.
- The session cookie is not marked Secure when `PUBLIC_URL` is blank, even if a proxy terminates TLS. Set `PUBLIC_URL=https://...` for public tunnel use.
- There is currently no optional extraction of arbitrary original webpage HTML, no arbitrary enclosure download storage, and no inbound webhook execution surface.
- Conservative port restrictions reject some legitimate feeds on uncommon public ports.
- Backup archives include secrets and potentially private feed content; protect their filesystem permissions and off-site copies.

## Deployment guidance

1. Install on a maintained Debian/Ubuntu system and update Docker and packages regularly.
2. Keep `data/` owned by UID/GID 10001 and `.env` readable only by the host administrator.
3. Never set the Compose bind host to `0.0.0.0` without additional network isolation and HTTPS.
4. Set `PUBLIC_URL` to the exact Cloudflare HTTPS hostname.
5. Use Cloudflare Access, WAF/rate limiting if externally accessible, and block other access paths.
6. Back up and test restoration of the SQLite database.
7. Watch the activity log for repeated failing feeds, invalid XML, or unexpected retrieval attempts.
8. Apply updated dependencies after testing in a nonproduction environment.

For vulnerabilities, privately report the issue to the repository maintainer; avoid exposing exploit payloads publicly before a fix is available.
