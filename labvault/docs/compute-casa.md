# Adding LabVault to compute.casa

LabVault runs as its own service on your Mac mini and is exposed at a subdomain
(e.g. `labs.compute.casa`). This doc covers the homelab tile and the reverse
proxy. Because there's no in-app auth, **put LabVault behind your normal homelab
access control** (Tailscale, VPN, or a proxy auth layer) — see the bottom.

## 1. The tile

Add a card to your compute.casa homepage linking to the subdomain. Drop this
wherever your tiles live:

```html
<a class="tile" href="https://labs.compute.casa">
  <div class="tile-icon">🧪</div>
  <div class="tile-title">LabVault</div>
  <div class="tile-desc">Lab results, tracked over time — private &amp; local</div>
  <div class="tile-stat" data-labvault-stat>&nbsp;</div>
</a>
```

### Live stats via Hermes

LabVault exposes a JSON summary Hermes can poll to decorate the tile (e.g. show
"3 out of range"). No auth, same-origin on the LAN:

```js
// Run from Hermes or a small script on the homepage.
async function updateLabTile() {
  try {
    const r = await fetch("https://labs.compute.casa/api/summary");
    const s = await r.json();
    const el = document.querySelector("[data-labvault-stat]");
    if (!el) return;
    if (s.latest_out_of_range > 0) {
      el.textContent = `⚠ ${s.latest_out_of_range} out of range`;
      el.style.color = "#f85149";
    } else {
      el.textContent = `✓ ${s.markers} markers tracked`;
    }
  } catch (_) { /* offline — leave tile as-is */ }
}
updateLabTile();
setInterval(updateLabTile, 300000); // every 5 min
```

`GET /api/summary` returns:

```json
{"reports": 3, "results": 27, "markers": 9,
 "needs_review": 4, "latest_out_of_range": 3, "last_collected": "2024-07-01"}
```

Other endpoints Hermes can use:

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | counts + latest-out-of-range for the tile |
| `GET /api/markers` | every tracked marker with its latest value & range |
| `GET /api/marker/{id}` | full time series for one marker |
| `GET /export.csv` | all data as CSV (add `?marker=ID` to scope) |
| `GET /export.json` | all data as JSON |
| `GET /healthz` | `{"status":"ok"}` liveness probe |

## 2. Reverse proxy for the subdomain

### Caddy (recommended — automatic HTTPS)

```caddy
labs.compute.casa {
    reverse_proxy 127.0.0.1:8087
}
```

### nginx

```nginx
server {
    server_name labs.compute.casa;
    location / {
        proxy_pass http://127.0.0.1:8087;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 50m;   # large multi-page PDFs
    }
}
```

## 3. Access control (do this — there's no in-app auth)

Pick whichever your homelab already uses:

- **Tailscale / WireGuard**: bind LabVault to the tailnet or LAN only and reach
  `labs.compute.casa` over the VPN. Set `LABVAULT_HOST=127.0.0.1` and let the
  proxy (also on the tailnet) forward to it.
- **Caddy basic auth** in front of the reverse_proxy:
  ```caddy
  labs.compute.casa {
      basic_auth {
          niko <bcrypt-hash>   # caddy hash-password
      }
      reverse_proxy 127.0.0.1:8087
  }
  ```
- **Tailscale Serve**: `tailscale serve https / http://127.0.0.1:8087` keeps it
  entirely inside your tailnet with no public exposure at all.

Whatever you choose, LabVault's own privacy guarantee still holds regardless: it
never sends your data to a cloud model.
