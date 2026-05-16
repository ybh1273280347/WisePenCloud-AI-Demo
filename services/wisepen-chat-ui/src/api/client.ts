const DEFAULT_BASE_URL = "";

export const apiBaseUrl = (
  import.meta.env.VITE_CHAT_API_BASE_URL || DEFAULT_BASE_URL
).replace(/\/+$/, "");

const fromSourceSecret = import.meta.env.VITE_FROM_SOURCE_SECRET || "";
const debugAuthToken = import.meta.env.VITE_DEBUG_AUTH_TOKEN || "";

function userId(): string {
  const storageKey = "wisepen-chat-ui-user-id";
  const existing = window.localStorage.getItem(storageKey);
  if (existing) {
    return existing;
  }

  const generated = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  window.localStorage.setItem(storageKey, generated);
  return generated;
}

export function makeHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("X-User-Id", userId());

  if (fromSourceSecret) {
    headers.set("X-From-Source", fromSourceSecret);
  }
  if (debugAuthToken) {
    headers.set("Authorization", `Bearer ${debugAuthToken}`);
  }

  return headers;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: makeHeaders(init.headers),
  });
  return response;
}

export type ApiEnvelope<T> = {
  code?: number;
  msg?: string;
  message?: string;
  data?: T;
};

export async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Expected JSON but received HTTP ${response.status}: ${text.slice(0, 300)}`);
  }
}

export async function readApiData<T>(response: Response, fallbackMessage: string): Promise<T> {
  const body = await readJson<ApiEnvelope<T>>(response);
  if (!response.ok || body.code !== 200 || body.data == null) {
    throw new Error(
      body.msg ||
        body.message ||
        `${fallbackMessage}. HTTP ${response.status}. Check VITE_FROM_SOURCE_SECRET and proxy target.`,
    );
  }
  return body.data;
}

export async function ensureApiOk(response: Response, fallbackMessage: string): Promise<void> {
  const body = await readJson<ApiEnvelope<unknown>>(response);
  if (!response.ok || body.code !== 200) {
    throw new Error(
      body.msg ||
        body.message ||
        `${fallbackMessage}. HTTP ${response.status}. Check VITE_FROM_SOURCE_SECRET and proxy target.`,
    );
  }
}
