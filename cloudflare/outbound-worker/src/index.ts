interface Env {
  AI: Ai;
  MOY_APP_API_BASE_URL: string;
  MOY_APP_API_TOKEN?: string;
  PREVIEW_BASE_DOMAIN: string;
  WORKERS_AI_MODEL: string;
}

type BusinessInput = {
  name?: string;
  category?: string;
  city?: string;
  state?: string;
  country?: string;
  phone?: string;
  email?: string;
  website?: string;
  google_maps_url?: string;
  place_id?: string;
  rating?: number;
  review_count?: number;
  services?: string[];
};

type DemoPayload = {
  business: BusinessInput;
  demo?: Record<string, unknown>;
  outreach?: Record<string, unknown>;
  lead?: Record<string, unknown>;
};

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "access-control-allow-headers": "content-type,authorization",
      ...init.headers,
    },
  });
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

async function readJson<T>(request: Request): Promise<T> {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Response("Expected application/json", { status: 415 });
  }
  return request.json() as Promise<T>;
}

function deterministicCopy(business: BusinessInput) {
  const name = business.name || "This business";
  const category = business.category || "local service";
  const place = [business.city, business.state].filter(Boolean).join(", ");
  const locationLine = place ? ` in ${place}` : "";
  const services = business.services?.length
    ? business.services.slice(0, 6)
    : ["Fast response", "Clear pricing", "Local service", "Customer support"];

  return {
    title: `${name} - ${category}${locationLine}`,
    hero: `${name}`,
    subhero: `${category} services${locationLine}, built around calls, bookings, and local trust.`,
    cta: business.phone ? `Call ${business.phone}` : "Request a quote",
    services,
    metaDescription: `${name} provides ${category} services${locationLine}. View the website preview prepared by Moydus.`,
  };
}


function extractModelText(aiResult: unknown): string {
  if (!aiResult || typeof aiResult !== "object") return "";
  const result = aiResult as Record<string, unknown>;
  if (typeof result.response === "string") return result.response;
  const choices = result.choices;
  if (Array.isArray(choices) && choices.length > 0) {
    const first = choices[0] as Record<string, unknown>;
    if (typeof first.text === "string") return first.text;
  }
  return "";
}

function parseJsonObjectFromText(text: string): Record<string, unknown> | null {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced?.[1] || text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return null;

  try {
    const parsed = JSON.parse(candidate.slice(start, end + 1));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}


function businessAllowsAlwaysOpenClaim(business: BusinessInput): boolean {
  const source = JSON.stringify(business).toLowerCase();
  return source.includes("24/7") || source.includes("24-hour") || source.includes("24 hour") || source.includes("around the clock");
}

function hasAlwaysOpenClaim(value: string): boolean {
  const text = value.toLowerCase();
  return text.includes("24/7") || text.includes("24-hour") || text.includes("24 hour") || text.includes("around the clock");
}

function normalizeGeneratedCopy(parsed: Record<string, unknown>, fallback: ReturnType<typeof deterministicCopy>, business: BusinessInput) {
  const allowAlwaysOpenClaim = businessAllowsAlwaysOpenClaim(business);
  const services = Array.isArray(parsed.services)
    ? parsed.services
        .filter((item): item is string => typeof item === "string")
        .filter((item) => allowAlwaysOpenClaim || !hasAlwaysOpenClaim(item))
        .slice(0, 6)
    : fallback.services;

  const name = business.name?.trim();
  const phone = business.phone?.trim();
  const title = typeof parsed.title === "string" && parsed.title.trim() ? parsed.title.trim() : fallback.title;
  const hero = typeof parsed.hero === "string" && parsed.hero.trim() ? parsed.hero.trim() : fallback.hero;
  const cta = typeof parsed.cta === "string" && parsed.cta.trim() ? parsed.cta.trim() : fallback.cta;
  const metaDescription = typeof parsed.metaDescription === "string" && parsed.metaDescription.trim()
    ? parsed.metaDescription.trim()
    : fallback.metaDescription;

  const subhero = typeof parsed.subhero === "string" && parsed.subhero.trim() ? parsed.subhero.trim() : fallback.subhero;
  const safeMetaDescription = name && !metaDescription.toLowerCase().includes(name.toLowerCase())
    ? fallback.metaDescription
    : metaDescription;

  return {
    title: name && !title.toLowerCase().includes(name.toLowerCase()) ? fallback.title : title,
    hero: name && !hero.toLowerCase().includes(name.toLowerCase()) ? fallback.hero : hero,
    subhero: !allowAlwaysOpenClaim && hasAlwaysOpenClaim(subhero) ? fallback.subhero : subhero,
    cta: phone && !cta.includes(phone) ? fallback.cta : cta,
    services: services.length ? services : fallback.services,
    metaDescription: !allowAlwaysOpenClaim && hasAlwaysOpenClaim(safeMetaDescription)
      ? fallback.metaDescription
      : safeMetaDescription,
  };
}

async function generateBusinessCopy(request: Request, env: Env): Promise<Response> {
  const business = await readJson<BusinessInput>(request);
  const fallback = deterministicCopy(business);

  if (!env.AI) {
    return json({ source: "fallback", copy: fallback });
  }

  const prompt = [
    "Return JSON only. No markdown, no prose, no explanation.",
    "You write concise website preview copy for US local businesses.",
    "Required JSON keys: title, hero, subhero, cta, services, metaDescription.",
    "services must be an array of 3 to 6 short strings.",
    "Do not invent addresses, licenses, awards, guarantees, or 24/7 claims.",
    "Keep it specific to the business data.",
    `Business data: ${JSON.stringify(business)}`,
  ].join("\n");

  try {
    const aiResult = await env.AI.run(env.WORKERS_AI_MODEL || "@cf/meta/llama-3.2-3b-instruct", {
      prompt,
      max_tokens: 220,
    });
    const text = extractModelText(aiResult);
    const parsed = parseJsonObjectFromText(text);
    if (!parsed) {
      return json({ source: "fallback", copy: fallback, warning: "Workers AI returned non-JSON output", raw: text.slice(0, 1000) });
    }
    return json({ source: "workers-ai", copy: normalizeGeneratedCopy(parsed, fallback, business), fallback });
  } catch (error) {
    return json({
      source: "fallback",
      copy: fallback,
      warning: error instanceof Error ? error.message : "Workers AI request failed",
    });
  }
}

async function buildPreviewRequest(request: Request, env: Env): Promise<Response> {
  const business = await readJson<BusinessInput>(request);
  const base = env.PREVIEW_BASE_DOMAIN || "moydus.site";
  const subdomain = slugify([business.name, business.city].filter(Boolean).join(" ") || crypto.randomUUID());

  return json({
    business,
    subdomain,
    preview_url: `https://${subdomain}.${base}`,
    template_query: {
      category: business.category,
      city: business.city,
      state: business.state,
    },
    next_step: "Run scripts/run_outbound_demo.py with this business payload and preview URL after deployment.",
  });
}

async function registerDemo(request: Request, env: Env): Promise<Response> {
  const payload = await readJson<DemoPayload>(request);
  const apiBase = (env.MOY_APP_API_BASE_URL || "").replace(/\/$/, "");
  if (!apiBase) {
    return json({ error: "MOY_APP_API_BASE_URL is not configured" }, { status: 500 });
  }

  const headers: Record<string, string> = {
    "content-type": "application/json",
    "accept": "application/json",
  };
  if (env.MOY_APP_API_TOKEN) {
    headers.authorization = `Bearer ${env.MOY_APP_API_TOKEN}`;
  }

  const response = await fetch(`${apiBase}/outbound/demo-sites`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });

  const text = await response.text();
  return new Response(text, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") || "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
    },
  });
}

export default {
  async fetch(request, env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return json({ ok: true });
    }

    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "moydus-outbound-worker" });
    }

    if (request.method === "POST" && url.pathname === "/api/business-copy") {
      return generateBusinessCopy(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/preview-request") {
      return buildPreviewRequest(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/register-demo") {
      return registerDemo(request, env);
    }

    return json({ error: "Not found" }, { status: 404 });
  },
} satisfies ExportedHandler<Env>;
