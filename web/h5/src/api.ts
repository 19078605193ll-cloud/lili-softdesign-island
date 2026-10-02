export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
let csrf = "";
let initialization: Promise<void> | null = null;
export function resetAuth() {
  csrf = "";
  sessionStorage.clear();
}
export async function initCsrf() {
  if (!initialization)
    initialization = fetch("/api/v1/auth/csrf", { credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) throw new Error("登录服务暂不可用");
        csrf = (await r.json()).csrf_token;
      })
      .finally(() => (initialization = null));
  return initialization;
}
export async function api<T = any>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    key?: string;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const method = options.method || "GET";
  if (method !== "GET" && !csrf) await initCsrf();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  try {
    const r = await fetch(path.startsWith("/api/") ? path : "/api/v2" + path, {
      method,
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        ...(options.key ? { "Idempotency-Key": options.key } : {}),
      },
      body:
        options.body instanceof FormData
          ? options.body
          : options.body === undefined
            ? undefined
            : JSON.stringify(options.body),
    });
    const data = await r.json();
    if (!r.ok) {
      const code = r.headers.get("X-Error-Code") || data.error?.code || "";
      if (r.status === 401 && !path.endsWith("/login"))
        window.dispatchEvent(new Event("session-expired"));
      if (code === "CSRF_TOKEN") {
        csrf = "";
        await initCsrf();
      }
      throw new ApiError(
        r.status,
        code,
        typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map((item: any) => String(item.msg || "输入无效").replace(/^Value error, /, "")).join("；")
          : data.error?.message || data.message || "请求失败，请重试",
      );
    }
    if (data.csrf_token) csrf = data.csrf_token;
    return data;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
}
