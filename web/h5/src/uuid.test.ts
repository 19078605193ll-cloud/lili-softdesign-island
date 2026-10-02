import { afterEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { randomUUID } from "./uuid";

afterEach(() => vi.unstubAllGlobals());

it("uses native UUID when available", () => {
  const native = vi.fn(() => "native-id");
  vi.stubGlobal("crypto", { randomUUID: native });
  expect(randomUUID()).toBe("native-id");
  expect(native).toHaveBeenCalledOnce();
});

it("creates unique UUID v4 values on HTTP without randomUUID", () => {
  vi.stubGlobal("crypto", {
    getRandomValues: webcrypto.getRandomValues.bind(webcrypto),
  });
  const ids = Array.from({ length: 1000 }, randomUUID);
  expect(new Set(ids).size).toBe(1000);
  for (const id of ids)
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

it("supports HTTP in the server-rendered admin template too", () => {
  const template = readFileSync(new URL("../../../app/imports/templates/base.html", import.meta.url), "utf8");
  const helper = template.match(/function randomUUID\(\) \{[\s\S]*?\n\}/)?.[0];
  expect(helper).toBeTruthy();
  const id = runInNewContext(helper + "\nrandomUUID()", {
    crypto: { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) },
  });
  expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});
