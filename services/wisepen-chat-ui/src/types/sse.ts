export type SseEvent = Record<string, unknown> & {
  type?:
    | "start"
    | "finish"
    | "text-start"
    | "text-delta"
    | "text-end"
    | "reasoning-start"
    | "reasoning-delta"
    | "reasoning-end"
    | "tool-input-start"
    | "tool-input-available"
    | "tool-output-available"
    | "start-step"
    | "finish-step"
    | "source-url"
    | "error"
    | "abort";
  _raw?: string;
  messageId?: string;
  id?: string;
  delta?: string;
  toolCallId?: string;
  toolName?: string;
  input?: unknown;
  output?: unknown;
  errorText?: string;
  reason?: string;
  sourceId?: string;
  url?: string;
};
