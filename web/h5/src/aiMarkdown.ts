import MarkdownIt from "markdown-it";
import { katex } from "@mdit/plugin-katex";

// Parse math before Markdown's escape rule removes the bracket delimiters.
const markdown = new MarkdownIt({ html: false, linkify: false }).use(katex, {
  delimiters: "all",
  throwOnError: false,
  trust: false,
  logger: () => "ignore",
  maxSize: 10,
  maxExpand: 1000,
});

export function renderAiMarkdown(text: string): string {
  return markdown.render(text);
}
