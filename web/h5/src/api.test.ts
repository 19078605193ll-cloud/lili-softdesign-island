import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { api, resetAuth, ApiError } from "./api";
describe("learner transport", () => {
  it("sends multipart data with CSRF and a browser-generated content type", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf" })),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ avatar_url: "/avatar" })),
      );
    vi.stubGlobal("fetch", fetcher);
    const form = new FormData();
    form.append("x", "0");
    await api("/api/v1/auth/me/avatar", { method: "PUT", body: form });
    expect(fetcher.mock.calls[1][1].body).toBe(form);
    expect(fetcher.mock.calls[1][1].headers["Content-Type"]).toBeUndefined();
    expect(fetcher.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("csrf");
  });
  beforeEach(() => {
    vi.stubGlobal("sessionStorage", { clear: vi.fn() });
    vi.stubGlobal("window", { dispatchEvent: vi.fn() });
    resetAuth();
  });
  afterEach(() => vi.unstubAllGlobals());
  it("reuses the caller submission key after an ambiguous network failure", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf" })),
      )
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "attempt" })));
    vi.stubGlobal("fetch", fetcher);
    const options = {
      method: "POST",
      body: { answers: { part: "A" } },
      key: "stable-submission-key",
    };
    await expect(api("/learning/attempts", options)).rejects.toThrow("network");
    await expect(api("/learning/attempts", options)).resolves.toEqual({
      id: "attempt",
    });
    expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe(
      fetcher.mock.calls[2][1].headers["Idempotency-Key"],
    );
    expect(fetcher.mock.calls[2][1].headers["X-CSRF-Token"]).toBe("csrf");
  });
  it("surfaces version conflict without silently resubmitting answers", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf" })),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "题目已更新" }), {
          status: 409,
          headers: { "X-Error-Code": "CONTENT_CHANGED" },
        }),
      );
    vi.stubGlobal("fetch", f);
    await expect(
      api("/learning/attempts", { method: "POST", body: {}, key: "k" }),
    ).rejects.toMatchObject({ status: 409, code: "CONTENT_CHANGED" });
    expect(f).toHaveBeenCalledTimes(2);
  });
  it("invalidates learner state on expired session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "登录已过期" }), {
          status: 401,
        }),
      ),
    );
    await expect(api("/learning/preferences")).rejects.toBeInstanceOf(ApiError);
    expect(window.dispatchEvent).toHaveBeenCalledOnce();
  });
});
