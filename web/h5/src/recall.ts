export type RecallType = "table" | "sentence" | "relation";
export const recallNames: Record<RecallType, string> = {
  table: "对比记忆",
  sentence: "要点填空",
  relation: "关系推演",
};
export interface RecallToken {
  text?: string | null;
  blank_key?: string | null;
}
export type RecallTableCell = RecallToken | RecallToken[];
export type RecallContent = { note: string } & (
  | { type: "table"; columns: string[]; rows: RecallTableCell[][] }
  | { type: "sentence"; paragraphs: RecallToken[][] }
  | {
      type: "relation";
      nodes: { id: string; label: RecallToken; x: number; y: number }[];
      edges: { source: string; target: string; label: RecallToken }[];
      steps: RecallToken[][];
    }
);
export interface RecallBlank {
  key: string;
  label: string;
  answer?: string;
  draft?: string;
  hint?: string;
  mastered?: boolean;
  needs_retest?: boolean;
  round_error_count?: number;
  historical_error_count?: number;
}
export interface RecallItem {
  id: string;
  version_id: string;
  title: string;
  type: RecallType;
  total_blanks: number;
  mastered: number;
  pending: number;
  completed: boolean;
  session_id: string | null;
  phase: string | null;
}
export interface RecallList {
  enabled: boolean;
  items: RecallItem[];
  resume_session_id: string | null;
  resume_recall_id?: string | null;
  resume_phase?: string | null;
  mastered: number;
  pending: number;
  completed: number;
}
export interface RecallDetail {
  id: string;
  subject_id: string;
  version_id: string;
  title: string;
  type: RecallType;
  content: RecallContent;
  blanks: RecallBlank[];
  knowledge: { id: string; name: string }[];
}
export interface RecallSession {
  slot_presentations: Record<string, RecallSlotPresentation>;
  id: string;
  recall_id: string;
  version_id: string;
  subject_id: string;
  title: string;
  type: RecallType;
  content: RecallContent;
  blanks: RecallBlank[];
  phase: "first" | "retest_ready" | "retest" | "completed";
  revision: number;
  current_key: string | null;
  completed: number;
  total: number;
  read_only: boolean;
  is_current_version: boolean;
}
export interface RecallSlotPresentation {
  value: string;
  outcome: "correct" | "incorrect";
}
export interface RecallFeedback {
  correct: boolean;
  blank_key: string;
  hint: string | null;
  answer: string | null;
  needs_retest: boolean;
}
