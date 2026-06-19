# Cloudflare setup for Moydus outbound previews

Moydus should use Cloudflare in two separate layers:

1. **Cloudflare Pages for static previews**
   - Generated Astro demo sites are static output.
   - Deploy them to `*.moydus.site` through Pages or a Pages-connected deploy repo.
   - The Python scraper still owns Playwright/Framer capture because Workers cannot run that pipeline.

2. **Cloudflare Worker for AI/proxy/orchestration helpers**
   - `cloudflare/outbound-worker` exposes small API endpoints for enrichment, preview request shaping, and moy-app registration.
   - Workers AI is optional. The Worker returns deterministic fallback copy if AI fails.

## Worker endpoints

- `GET /health`
- `POST /api/business-copy`
  - Input: Google Maps/enrichment business JSON.
  - Output: website copy suggestions.
- `POST /api/preview-request`
  - Input: business JSON.
  - Output: suggested subdomain and preview URL.
- `POST /api/register-demo`
  - Proxies the final outbound payload to `moy-app` at `/outbound/demo-sites`.

## Local Worker setup

```bash
cd cloudflare/outbound-worker
npm install
npx wrangler dev
```

Workers AI binding is configured in `wrangler.toml`:

```toml
[ai]
binding = "AI"
```

Set the backend token as a secret:

```bash
npx wrangler secret put MOY_APP_API_TOKEN
```

Deploy:

```bash
npx wrangler deploy
```


## Workers AI model strategy

`/api/business-copy` uses a fast JSON-mode model by default:

```toml
WORKERS_AI_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast"
```

For slower, higher-quality one-off copy generation, call:

```bash
curl -X POST "https://<worker>/api/business-copy?mode=quality" \
  -H "content-type: application/json" \
  -d '{"name":"Acme Locksmith","category":"Locksmith","city":"Austin","state":"TX"}'
```

Quality mode uses:

```toml
WORKERS_AI_QUALITY_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
```

Keep the fast model as the production default for outbound batches. The quality model can timeout under concurrent or cold requests, so it should be opt-in.

## Static preview deploy direction

Recommended flow:

1. Scraper generates Astro output with personalization and quality gate.
2. Deploy Astro output to Cloudflare Pages under a deterministic subdomain.
3. Register the deployed URL in `moy-app`.
4. Send email/SMS/WhatsApp with the preview URL.

The Worker should not replace the Python scraper. It should sit in front of the backend as a lightweight edge API for copy generation, payload shaping, and registration.


## Domain strategy

For one-off demos, a Pages custom domain like `austin-dermatology.moydus.com` is possible, but Cloudflare requires the custom domain to be attached to the Pages project, not only a DNS CNAME. If DNS exists without the Pages custom-domain binding, the preview can fail at the edge.

For outbound campaigns, prefer a preview domain that is separate from the main brand domain:

- single hand-picked demo: `austin-dermatology.moydus.com`
- bulk outbound previews: `austin-dermatology.moydus.site` or wildcard Worker routing under `*.moydus.site`

This keeps `moydus.com` clean for the agency/product site while allowing many short-lived previews under `moydus.site`.
