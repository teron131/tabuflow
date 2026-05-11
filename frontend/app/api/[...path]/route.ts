const API_BASE = process.env.DATA_AGENTICS_API_URL || "http://localhost:8017";

export const dynamic = "force-dynamic";

type RouteContext = {
	params: Promise<{ path?: string[] }>;
};

const HOP_BY_HOP_HEADERS = new Set([
	"connection",
	"content-length",
	"host",
	"keep-alive",
	"proxy-authenticate",
	"proxy-authorization",
	"te",
	"trailer",
	"transfer-encoding",
	"upgrade",
]);

function forwardedHeaders(request: Request) {
	const headers = new Headers();
	for (const [key, value] of request.headers.entries()) {
		if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
			headers.set(key, value);
		}
	}
	return headers;
}

async function proxy(request: Request, context: RouteContext) {
	const params = await context.params;
	const path = params.path?.join("/") || "";
	const incomingUrl = new URL(request.url);
	const target = new URL(`/api/${path}${incomingUrl.search}`, API_BASE);
	const method = request.method.toUpperCase();
	const body = method === "GET" || method === "HEAD" ? undefined : request.body;
	const init: RequestInit & { duplex?: "half" } = {
		method,
		headers: forwardedHeaders(request),
		body,
		cache: "no-store",
	};
	if (body) {
		init.duplex = "half";
	}

	return fetch(target, init);
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
