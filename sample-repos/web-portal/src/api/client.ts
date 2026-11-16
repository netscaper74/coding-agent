// Thin fetch wrapper used by every portal API call.

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  retries?: number;
  signal?: AbortSignal;
}

const RETRYABLE = new Set([429, 502, 503, 504]);

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, idempotencyKey, retries = 2, signal } = opts;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  for (let attempt = 0; ; attempt++) {
    const res = await fetch(`/bff${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      signal,
    });
    if (res.ok) return (await res.json()) as T;
    // Only retry idempotent requests, or non-idempotent ones that carry an idempotency key.
    const canRetry = RETRYABLE.has(res.status) && (method === "GET" || !!idempotencyKey);
    if (!canRetry || attempt >= retries) {
      const payload = await res.json().catch(() => undefined);
      throw new ApiError(res.status, `API ${method} ${path} failed with ${res.status}`, payload);
    }
    await new Promise((r) => setTimeout(r, 250 * 2 ** attempt));
  }
}
