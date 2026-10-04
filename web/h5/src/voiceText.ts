export function insertVoiceText(
  draft: string,
  transcript: string,
  start: number,
  end: number,
  limit = 2000,
) {
  if (!transcript.trim())
    return { text: draft, cursor: start, overflow: false };
  const from = Math.max(0, Math.min(start, draft.length));
  const to = Math.max(from, Math.min(end, draft.length));
  const text = draft.slice(0, from) + transcript + draft.slice(to);
  return text.length > limit
    ? { text: draft, cursor: from, overflow: true }
    : { text, cursor: from + transcript.length, overflow: false };
}
