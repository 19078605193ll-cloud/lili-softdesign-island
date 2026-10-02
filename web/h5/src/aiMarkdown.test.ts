import { describe, expect, it } from "vitest";
import { renderAiMarkdown } from "./aiMarkdown";

describe("AI answer math rendering", () => {
  it.each([String.raw`\(2^{12}\)`, "$2^{12}$"])(
    "renders inline exponents before Markdown consumes delimiters: %s",
    (formula) => {
      const html = renderAiMarkdown(`页面大小为 ${formula} B。`);
      expect(html).toContain('class="katex"');
      expect(html).toContain("<msup>");
      expect(html).not.toContain('class="katex-display"');
      expect(html).toContain("页面大小为");
    },
  );

  it.each([
    String.raw`\[2^{12}=4096\text{ 字节}\]`,
    "$$\n2^{12}=4096\\text{ 字节}\n$$",
  ])("renders display formulas with Chinese units: %s", (formula) => {
    const html = renderAiMarkdown(
      `12 位最多能表示：\n\n${formula}\n\n所以页面大小是 **4KB**。`,
    );
    expect(html).toContain('class="katex-display"');
    expect(html).toContain("<msup>");
    expect(html).toContain("字节");
    expect(html).toContain("<strong>4KB</strong>");
  });

  it("keeps code examples and Mermaid sources literal", () => {
    const html = renderAiMarkdown(
      "`$2^{12}$`\n\n```text\n\\(2^{12}\\)\n```\n\n```mermaid\nflowchart LR\nA[$x$] --> B[CPU]\n```",
    );
    expect(html).not.toContain('class="katex"');
    expect(html).toContain("<code>$2^{12}$</code>");
    expect(html).toContain('class="language-mermaid"');
    expect(html).toContain("A[$x$] --&gt; B[CPU]");
  });

  it("keeps escaped dollars, prices and unmatched delimiters as text", () => {
    const html = renderAiMarkdown(
      String.raw`价格 \$5 和 $10。未完成 $2^{12}，以及 \(x。`,
    );
    expect(html).not.toContain('class="katex"');
    expect(html).toContain("价格 $5 和 $10");
    expect(html).toContain("未完成 $2^{12}");
  });

  it("preserves the rest of an answer when a formula is invalid", () => {
    const html = renderAiMarkdown("之前\n\n$\\frac{$\n\n**之后**");
    expect(html).toContain("katex-error");
    expect(html).toContain("<strong>之后</strong>");
  });

  it("does not allow formulas to introduce links or images", () => {
    const html = renderAiMarkdown(
      String.raw`\(\href{javascript:alert(1)}{x}\)\n\(\includegraphics{https://example.com/x.png}\)`,
    );
    expect(html).not.toMatch(/<(a|img)\b/);
  });
});
