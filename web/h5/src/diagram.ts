/** Normalize harmless label line breaks without allowing model-provided HTML. */
export function diagramSource(value: string): string {
  const source = value.replace(/<br\s*\/?\s*>/gi, " · ");
  if (/click\s|%%\{|https?:|<|>\s*script/i.test(source)) {
    throw new Error("Unsupported diagram");
  }
  return source;
}
