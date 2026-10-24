# Users & Requests

Configure in **Settings → Users & Requests**.

## Authentication Methods

Shelfmark supports four authentication methods, configured in **Settings → Security**.

### Local

You create user accounts directly in Shelfmark with a username and password. At least one local admin account must exist before this mode can be enabled.

### Proxy Authentication

Your reverse proxy handles authentication and passes the username to Shelfmark via a header (e.g. `Remote-User`). Accounts are created automatically on first sign-in. If a local user with the same username already exists, the proxy identity will be linked to that account rather than creating a duplicate. Admin status can optionally be derived from a groups header.

### OIDC (OpenID Connect)

Users sign in through your identity provider. Accounts are created automatically on first login (unless auto-provisioning is disabled, in which case you need to pre-create them). If a local user with a matching verified email already exists, the OIDC identity will be linked to that account on first sign-in. Admin status can optionally be derived from a group claim.

A local admin account is required as a fallback. See [OIDC](oidc.md) for provider setup.

### Calibre-Web Database

User accounts are synced from your Calibre-Web `app.db`. If a local user with a matching email already exists, the CWA identity will be linked to that account. Roles are kept in sync with CWA. Users removed from CWA are cleaned up on the next sync.

Requires mounting your Calibre-Web `app.db` to `/auth/app.db`.

### If a method's requirements go missing

Once a method is selected, Shelfmark keeps requiring sign-in even if that method's requirements go missing later (a deleted local admin, a missing `app.db`, incomplete OIDC settings, or an unrecognized `AUTH_METHOD` value). It never falls back to "No Authentication" on its own. While Local or OIDC authentication is active, the last local admin can't be deleted or demoted.

If you're locked out, start the container once with `AUTH_METHOD=none`, create a local admin under **Settings → Users**, then remove the override. Keep Shelfmark off the public internet while the override is set.

## Per-User Settings

Admins can configure per-user settings by editing a user in the user management panel. Non-admin users can also edit their own settings through **My Account** (accessible from the user menu). Admins control which sections are visible in My Account via the **Visible Self-Settings Sections** option.

There are four categories of per-user settings:

### Delivery Preferences

Override where a user's downloads are sent. Options depend on the global output mode configuration:

- **Output mode** — Folder, Email (SMTP), or Grimmory (API)
- **Destination** — A custom folder path for this user's ebook downloads
- **Audiobook destination** — A custom folder path for audiobook downloads
- **Grimmory library/path** — Per-user Grimmory target (when using Grimmory output mode)
- **Email recipient** — Per-user email address (when using Email output mode)

### Search Preferences

Override how a user searches, on top of the global search defaults:

- **Search mode** — Direct or Universal for this user
- **Default book languages** — The languages a user's searches fall back to when they don't pick one themselves. Useful for a shared instance where readers want different languages.
- **Metadata providers** — Book, audiobook, and combined-mode provider for this user
- **Default release sources** — The release tab opened first for books and audiobooks

### Notifications

Users can configure personal notification routes, separate from the global notification settings. Each route targets a URL (e.g. an Apprise-compatible endpoint) and can be scoped to specific event types or all events.

Route URLs can use `{User}` and `{Email}` in their path or query, filled in from each user's account when a notification is sent. A route is skipped for users without the value it needs, or whose value contains spaces or any of `, ; / \ "` (or is only dots, such as `..`), since those would split it into several targets or change the URL path. The admin-set default routes apply to every user who hasn't set routes of their own and can use any service, so one default such as `mailtos://user:pass@smtp.example.com?to={Email}` reaches each user at their own address. The service a default points to then receives each user's email address, so only use one you trust with it. Routes a user sets themselves stay limited to the personal-route services, e.g. `ntfys://ntfy.sh/shelfmark-{User}`.

### Request Policy (admin-only)

Admins can override the default ebook/audiobook modes and request rules for individual users. See [Per-User Overrides](#per-user-overrides) below.

---

## Requests

The request system controls whether users can download directly or need admin approval first.

### Policy Modes

Each content type (ebook, audiobook) has a default mode that sets the baseline:

| Mode | Behaviour |
|------|-----------|
| **Download** | Users download directly, no approval needed |
| **Request Release** | Users pick a specific release, then submit it for admin approval |
| **Request Book** | Users request the book itself — an admin picks the release and fulfils it |
| **Blocked** | No downloads or requests allowed |

### Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Enable Requests | Master toggle. When off, everyone downloads directly | Off |
| Default Ebook Mode | Baseline mode for all ebook sources | Download |
| Default Audiobook Mode | Baseline mode for all audiobook sources | Download |
| Request Rules | Per-source overrides (see below) | None |
| Max Pending Requests Per User | Open request limit per user | 20 |
| Allow Notes on Requests | Let users attach a note when submitting | On |

### Request Rules

The rules matrix lets you override the mode for specific source + content type combinations. Rules can only be **equal to or more restrictive** than the content-type default — they cannot grant more access than the baseline.

For example, if the default ebook mode is "Download", a rule can restrict a specific source to "Request Release" or "Blocked", but not the other way around. If no rule matches, the content-type default applies.

Rules cannot use "Request Book". Sources whose browse results are already concrete releases are also treated as "Request Release" when the default mode is "Request Book", since there is no book-level request to make for them.

### Per-User Overrides

Admins can override the default ebook/audiobook modes and request rules for individual users. Per-user rules are overlaid on the global rules, not replacing them.

### Request Lifecycle

1. User submits a request (book or release level, depending on the resolved policy mode)
2. Request appears in the admin request queue as **pending**
3. Admin either **fulfils** (queues a download) or **rejects** the request
4. For fulfilled requests, delivery state is tracked through the download pipeline
5. If delivery fails and the download cannot be retried, the request goes back to pending automatically so an admin can approve it again with a different release. An admin can also reopen a rejected request
6. Users can cancel their own pending requests
7. An admin can **reopen** a rejected request, from the Requests tab or from History. It goes back to pending with what the user originally asked for, and shows in the activity list again even if the user had dismissed it.
