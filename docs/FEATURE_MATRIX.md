# Feature / requirements verification matrix

**Version assessed:** 0.1.1 (initial prototype). Status is based on code inspection and integration tests, not an independent production audit. This matrix aligns with the **13 sections of the uploaded xsukax RSS Aggregator development prompt**. The earlier 700+ item generic specification is broader than this implementation.

| Uploaded section | Status | Verified behavior / remaining gaps |
|---|---|---|
| 1. Thunderbird research and compatibility | Partial | Checked official Mozilla feed guide. Feed accounts, folders, message-style archive/read/star/tag, filters, saved searches, OPML. Not a 1:1 recreation of all Thunderbird UI, shortcuts, and advanced preferences. |
| 2. Python architecture and Docker install | Implemented, deployment unverified | FastAPI modular modules, SQLite WAL, Dockerfile/Compose, install.sh, backup.sh, uninstall.sh, data persisted. Docker engine unavailable during authoring, so no real Compose build or Debian integration test run. |
| 3. Tiny-VPS efficiency | Partial | One Uvicorn process, bounded downloader threads, batch insert transaction, pagination, SQLite indexes and FTS5, article retention. No high-volume benchmarks or tuning of dynamic concurrency settings. |
| 4. Cloudflare Tunnel / local HTTP | Configuration supplied, not field-tested | Loopback-only port, relative browser URLs, cookie Secure when PUBLIC_URL is HTTPS, Cloudflare origin instructions. Real Cloudflare Tunnel deployment not verified. |
| 5. Thunderbird-style responsive GUI | Core implemented | Three panes, mobile navigation, reading tabs, theme, adjustable pane width, sidebar, drag/drop feeds in same account, status/actions. No column editor, user-configurable shortcuts, tabbed articles, or desktop-grade context menus. |
| 6. Accounts, feeds, and folders | Mostly implemented | Multiple accounts, nested folders, discover feed URLs, rename, pause, move feed within account, delete. Feed favicon retrieval, HTTP authentication, copying subscriptions across accounts, and preserving detached articles after unsubscribe not implemented. |
| 7. Reliable updating | Core implemented | Polling, HTTP conditional fetch, 304, retries/backoff, bounded concurrency, manual and paused feed updates, errors. No distributed job queue, external scheduler, WebSub, or UI refresh-progress percentage. |
| 8. Standards / multilingual parsing | Core implemented | RSS 2.0, RDF/RSS 1.0, Atom 1.0, dates, categories, enclosures, metadata, revision checksum, Unicode, HTML sanitization. Nonstandard feed recovery and XML-internal XHTML content fidelity may vary; not certified against every syndication edge case. |
| 9. Article reading and management | Core implemented | Read/unread, star, bookmark, archive, Trash, bulk operations, tags, full feed content/summary/plain, external site, HTML article export, CSV. No embedded external sites, tabbed articles, actual downloaded enclosure files, permanent webpage copy, or detailed reader-mode extraction. |
| 10. Tagging and filtering | Partial to substantial | Automatic feed category tagging, manual tags, colors in database, condition groups, regex, priority, move/tag/star/delete/notify, import/export not supported. Filter UI has basic editor plus advanced JSON rather than complete graphical nested rule builder. No standalone rules import/export. |
| 11. Quick Filters, FTS search, smart folders | Core implemented | Server-side unread/starred/bookmarked/media filters, SQLite FTS5 boolean/phrase search, simple smart folder criteria, pagination. No highlighted result snippets, faceted global search, configurable search indexing, or article similarity. |
| 12. Storage, OPML, backup, extras | Partial | Local database, OPML nested import/export, logs, retention, CSV export, online database backup script. No complete configuration migration UI, IMAP sync, optional AI processing, full browser offline cache, or device-to-device sync. |
| 13. Security, verification, license, delivery | Partial | Random setup token, Argon2, CSRF, session hashing, local-only origin, XML/HTML/SSRF protections, tests, GPL-3.0 text. No independent penetration testing or real Docker/deployment verification. |

## Functionality implemented through tested Python endpoints

- `/api/auth/state`, `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/password`
- `/api/tree`, `/api/accounts`, `/api/folders`, `/api/feeds`, `/api/discover`, `/api/refresh`
- `/api/articles`, `/api/articles/{id}`, `/api/articles/bulk`, `/api/articles/{id}/tags`
- `/api/tags`, `/api/rules`, `/api/rules/{id}/run`, `/api/smart-folders`
- `/api/settings`, `/api/logs`, `/api/stats`, `/api/notifications`
- `/api/export/opml`, `/api/import/opml`, `/api/export/articles.csv`, `/api/export/article/{id}.html`
- `/api/maintenance/reindex`, `/healthz`

Some endpoints were exercised by tests while others are implemented but lack dedicated integration tests. **A passing test suite does not verify every endpoint.**

## Key missing items before claiming production completeness

1. Full visual filter builder with rule editing, nested groups, preview, and rule file import/export.
2. End-to-end refresh progress/events and richer feed-health dashboard.
3. Site-specific full-content extraction, permanent offline webpage archival, original page sandboxing.
4. Download manager for enclosures with controlled storage and progress.
5. Full multilingual locale translations; current UI labels are English and article text supports RTL.
6. Configurable keyboard shortcuts, tabbed article reader, per-folder reader settings.
7. True cross-device RSS synchronization, IMAP integration, optional notifications through webhooks.
8. Feed credentials stored safely, OAuth integrations, multi-user RBAC.
9. Extensive performance test on a large subscription dataset and resource measurements on a real VPS.
10. Verified container build, Docker setup on clean Debian 13, cloudflared production compatibility, independent security review.
11. Non-destructive article retention after feed unsubscribe / copying articles between accounts.
12. Complete migration tooling for future schema versions beyond the initial v1 schema.

## What the automated tests actually cover

- RSS 2.0 and Atom parsing of synthetic fixtures, publisher categories, enclosures.
- Unsafe XML DTD rejection and HTML script/event attribute sanitization.
- Private/unsafe URL rejection at the URL validation layer.
- First-run setup token, authentication, session persistence, CSRF enforcement, signout.
- Account, folder, and feed creation.
- Feed ingestion, duplicate-free 304 refresh, and metadata update.
- Indexed FTS title search, listing, read/star/bookmark updates, manual tagging.
- Filter creation, manual execution, smart search creation.
- OPML export and roundtrip parse, CSV export.
- Rebuild index, change theme settings, stats and logs.

Also performed JavaScript syntax checking and an isolated browser rendering test with a fully **mocked backend** on desktop and mobile. Real browser-to-server navigation was unavailable in the execution environment; therefore the live browser integration was not confirmed.
