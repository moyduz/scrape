# Moydus Outbound Preview Flow

## One-command URL to demo flow

This is the intended operator flow: pass one reference/template URL plus business data. The script clones the URL, creates a deploy branch, writes a moy-app payload, and can register the backend record.

```bash
.venv/bin/python scripts/run_outbound_demo.py "https://template.example.com" \
  --business-name "Acme Locksmith" \
  --business-category "Locksmith" \
  --business-email "owner@example.com" \
  --business-phone "+15125550123" \
  --business-city "Austin" \
  --business-state "TX" \
  --subdomain "acme-locksmith" \
  --template-key "locksmith" \
  --deploy-repo-dir "$HOME/Sites/moydus-demo-sites" \
  --deploy-remote "git@github.com:YOUR_ORG/moydus-demo-sites.git" \
  --push \
  --preview-url "https://acme-locksmith.moydus.site" \
  --outreach-channel email \
  --outreach-recipient "owner@example.com" \
  --register-backend \
  --api-base-url "https://app.moydus.com/api"
```

If you omit `--push`, the branch is committed only locally. If you omit `--register-backend`, the script still writes `data/outbound/<subdomain>.json` for later registration.

This repo can generate a deployable Astro/Next preview from a reference/template URL and register the generated preview in `moy-app`.


## Preview personalization and quality gate

Raw Astro previews are not blind embeds. Before files are written, the generator now:

- removes Framer badge/editor/search-index artifacts and Framer template CTA links
- keeps required Framer CSS, fonts, images, and layout assets
- updates title, description, Open Graph, and Twitter metadata for the target business
- injects the business name into the visible brand/hero area when possible
- rewrites phone text, `tel:` links, and contact/quote CTAs when a phone/email is provided
- writes `quality-report.json` inside the generated Astro output

`scripts/run_outbound_demo.py` blocks deploy when the static quality report fails. Typical failures are: Framer badge remains, Framer/template links remain, business name is missing, or provided phone is missing. Use `--allow-quality-fail` only when you inspected the preview manually and still want to deploy it.

Example with quality gate enabled by default:

```bash
.venv/bin/python scripts/run_outbound_demo.py "https://template.example.com" \
  --business-name "Acme Locksmith" \
  --business-category "Locksmith" \
  --business-phone "+15125550123" \
  --business-city "Austin" \
  --business-state "TX" \
  --subdomain "acme-locksmith" \
  --deploy-repo-dir "$HOME/Sites/moydus-demo-sites"
```

## 1. Generate a preview payload without registering

Use this when the site is generated locally but not deployed yet.

```bash
.venv/bin/python main.py "https://template.example.com" \
  --clone-mode astro-raw \
  --skip-ai \
  --outbound-payload-output data/outbound/acme-locksmith.json \
  --business-name "Acme Locksmith" \
  --business-category "Locksmith" \
  --business-phone "+15125550123" \
  --business-email "owner@example.com" \
  --business-city "Austin" \
  --business-state "TX" \
  --business-country "US" \
  --template-key "locksmith" \
  --industry "Home Services" \
  --subdomain "acme-locksmith" \
  --outreach-channel email \
  --outreach-recipient "owner@example.com" \
  --outreach-campaign "locksmith-austin-preview"
```

The payload will use a placeholder preview URL until deployment provides the real URL.

## 2. Register after deploy

After deploy produces a public preview URL, either run `main.py` with `--register-backend` and `--preview-url`, or register an existing payload:

```bash
.venv/bin/python scripts/register_demo_site.py data/outbound/acme-locksmith.json \
  --api-base-url "http://localhost:8000/api"
```

For production:

```bash
.venv/bin/python scripts/register_demo_site.py data/outbound/acme-locksmith.json \
  --api-base-url "https://app.moydus.com/api" \
  --api-token "$MOY_APP_API_TOKEN"
```

## 3. Direct generate + register

Use this after an external deploy step already knows the preview URL.

```bash
.venv/bin/python main.py "https://template.example.com" \
  --clone-mode astro-raw \
  --preview-url "https://acme-locksmith.moydus.site" \
  --screenshot-url "https://cdn.moydus.com/screenshots/acme-locksmith.png" \
  --register-backend \
  --api-base-url "https://app.moydus.com/api" \
  --business-name "Acme Locksmith" \
  --business-category "Locksmith" \
  --business-email "owner@example.com" \
  --template-key "locksmith" \
  --outreach-channel email \
  --outreach-recipient "owner@example.com"
```

## Payload shape

```json
{
  "business": {
    "name": "Acme Locksmith",
    "category": "Locksmith",
    "phone": "+15125550123",
    "email": "owner@example.com",
    "city": "Austin",
    "state": "TX",
    "country": "US"
  },
  "demo": {
    "template_key": "locksmith",
    "preview_url": "https://acme-locksmith.moydus.site",
    "screenshot_url": "https://cdn.moydus.com/screenshots/acme-locksmith.png",
    "status": "generated"
  },
  "outreach": {
    "channel": "email",
    "recipient": "owner@example.com",
    "campaign": "locksmith-austin-preview",
    "status": "draft"
  }
}
```

`moy-app` stores this as:

- `prospect_businesses`: Google Maps/business profile
- `demo_sites`: generated preview URL and deploy metadata
- `outreach_messages`: email/SMS/WhatsApp delivery record
- `leads`: optional, created when an email is available
