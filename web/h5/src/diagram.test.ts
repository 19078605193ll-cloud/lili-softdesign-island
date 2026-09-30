import { describe, expect, it } from "vitest";
import { diagramSource } from "./diagram";
describe("model diagram normalization", () => {
  it("accepts the real model's line-break label while removing its HTML", () => {
    expect(diagramSource("flowchart TD\nA[存储器<br/>统一存放指令和数据] --> B[CPU]"))
      .toBe("flowchart TD\nA[存储器 · 统一存放指令和数据] --> B[CPU]");
  });
  it.each(["A[<img src=x onerror=alert(1)>]", "click A callback", "%%{init: {}}%%", "A[https://example.com]"])
    ("continues to reject executable or external content: %s", (value) => {
      expect(() => diagramSource(value)).toThrow("Unsupported diagram");
    });
});
