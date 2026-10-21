# Reverse Proxy & Subpath Hosting

Shelfmark can run behind a reverse proxy at the root path (recommended) or under a subpath like `/shelfmark`.

## Root path setup (Recommended)

If you can serve Shelfmark at the root path (`https://shelfmark.example.com/`), leave `URL_BASE` empty. This is the simplest option and avoids extra subpath configuration.

Define this once in your Nginx `http` block so websocket upgrades are only sent when the client actually requests them:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

```nginx
server {
    listen 443 ssl;
    server_name shelfmark.example.com;

    location / {
        proxy_pass http://shelfmark:8084;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }
}
```

Keep the read timeout at least as high as `RELEASE_SEARCH_TIMEOUT` (300 seconds by default). Nginx's own default is 60 seconds, which cuts off any release search that has to solve a protection challenge first. See [Searches that time out behind a proxy](#searches-that-time-out-behind-a-proxy).

## Subpath setup

Running Shelfmark under a subpath like `/shelfmark` is supported without extra rewrite rules.

### 1. Set the base path in Shelfmark

- **UI**: Settings → Advanced → Base Path → `/shelfmark/`
- **Environment variable**: `URL_BASE=/shelfmark/`

### 2. Configure your reverse proxy

All Shelfmark paths (UI, API, assets, Socket.IO) are served under the base path. A single location block is enough.

---

### Without Authentication Proxy

**Complete Nginx configuration for subpath deployment:**

```nginx
location /shelfmark/ {
    proxy_pass http://shelfmark:8084/shelfmark/;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}
```

---

### With Authentication Proxy (Authelia, Authentik, etc.)

Shelfmark supports Proxy Authentication. When enabled, Shelfmark trusts the authenticated user from headers set by your auth proxy.

#### Shelfmark Settings

Configure in Settings → Security:

| Setting | Value |
|---------|-------|
| Authentication Method | Proxy Authentication |
| Proxy Auth User Header | `Remote-User` |
| Proxy Auth Logout URL | `https://auth.example.com/logout` |
| Proxy Auth Admin Group Header | `Remote-Groups` |
| Proxy Auth Admin Group Name | `admins` (or your admin group) |
| Proxy Auth Default Role | `User` — first-time users are regular users; the very first account is still made admin. Only consulted when no admin group is set |

#### Nginx Configuration with Authelia

This example uses Authelia snippets. Adapt for your auth proxy.

**Authelia auth request snippet** (`/etc/nginx/snippets/authelia-authrequest.conf`):

```nginx
location /authelia {
    internal;
    proxy_pass http://authelia:9091/api/authz/auth-request;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Host $host;
    proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
    proxy_set_header X-Original-Method $request_method;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Authelia location snippet** (`/etc/nginx/snippets/authelia-location.conf`):

```nginx
auth_request /authelia;
auth_request_set $target_url $scheme://$http_host$request_uri;
auth_request_set $user $upstream_http_remote_user;
auth_request_set $groups $upstream_http_remote_groups;
auth_request_set $name $upstream_http_remote_name;
auth_request_set $email $upstream_http_remote_email;
proxy_set_header Remote-User $user;
proxy_set_header Remote-Groups $groups;
proxy_set_header Remote-Name $name;
proxy_set_header Remote-Email $email;
error_page 401 =302 https://auth.example.com/?rd=$target_url;
```

**Complete Nginx configuration with Authelia:**

```nginx
# Include Authelia auth endpoint in your server block
include /etc/nginx/snippets/authelia-authrequest.conf;

# Main shelfmark location
location /shelfmark/ {
    include /etc/nginx/snippets/authelia-location.conf;

    proxy_pass http://shelfmark:8084/shelfmark/;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}
```

---

## Troubleshooting false network errors

If login, settings saves, or downloads appear to fail in the browser but the action still completes on the server, check your proxy headers first.

- Do not force `Connection: upgrade` on every request. That can break normal `POST` and `PUT` responses while the backend still processes them.
- If your proxy UI does not support conditional websocket headers, remove the forced websocket headers entirely and let Shelfmark fall back to polling.
- Keep the standard forwarded headers: `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` when using a subpath or OIDC.
- Preserve the original port in `Host` and `X-Forwarded-Host` by using `$http_host` rather than `$host` when Shelfmark is exposed on a custom port.

This is especially relevant for Nginx Proxy Manager or custom advanced config snippets that add websocket headers globally.

## Searches that time out behind a proxy

A release search can legitimately take minutes. When a source's protection cookies expire, the next search has to solve a browser challenge before it can run. Shelfmark gives a search up to `RELEASE_SEARCH_TIMEOUT` seconds (300 by default) and says why when it gives up, but a proxy with a shorter read timeout cuts the request off first. The search then fails with a message about the reverse proxy, or, on older versions, with "Unable to reach download source. Network may be restricted or mirrors blocked." A retry often works because the solve finished on the server after the proxy stopped waiting.

- Set the proxy's read timeout to at least `RELEASE_SEARCH_TIMEOUT`. In Nginx and Nginx Proxy Manager (the **Advanced** tab of the proxy host), add the `proxy_read_timeout` and `proxy_send_timeout` lines from the examples above.
- Cloudflare's proxy gives up after 100 seconds with a `524` error, and only Enterprise plans can raise that. If searches outlast it, reach Shelfmark without going through Cloudflare's proxy, for example on your LAN or over a VPN.
- To confirm the proxy is the cause, run the same search against Shelfmark directly at `http://<host>:8084`. If it succeeds there, or fails with a specific reason, the proxy was cutting it off.

---

## Health checks

Health checks work at `/shelfmark/api/health` when using a subpath configuration.
