import { describe, expect, it } from "vitest";
import { insertVoiceText } from "./voiceText";

describe("voice insertion preserves the draft", () => {
  it("inserts at the saved cursor and moves the cursor after the result", () => {
    expect(insertVoiceText("你好世界", "语音", 2, 2)).toEqual({
      text: "你好语音世界",
      cursor: 4,
      overflow: false,
    });
  });
  it("replaces only the selected range", () => {
    expect(insertVoiceText("解释这个知识点", "内存分页", 2, 6)).toEqual({
      text: "解释内存分页点",
      cursor: 6,
      overflow: false,
    });
  });
  it("keeps the draft for empty results and over-limit insertion", () => {
    expect(insertVoiceText("草稿", "  ", 1, 1).text).toBe("草稿");
    expect(insertVoiceText("a".repeat(1999), "完整结果", 1999, 1999)).toEqual({
      text: "a".repeat(1999),
      cursor: 1999,
      overflow: true,
    });
  });
  it("accepts an exact-limit result including a replacement", () => {
    const inserted = insertVoiceText("a".repeat(2000), "新", 100, 101);
    expect(inserted.overflow).toBe(false);
    expect(inserted.text.length).toBe(2000);
    expect(inserted.text[100]).toBe("新");
  });
});
