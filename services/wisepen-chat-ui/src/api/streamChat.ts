import {apiBaseUrl, makeHeaders} from "./client";
import {parseSseBlock, splitSseBuffer} from "./sseParser";
import type {SseEvent} from "../types/sse";

type StreamChatOptions = {
  sessionId: string;
  query: string;
  modelId?: number | null;
  signal: AbortSignal;
  onEvent: (event: SseEvent) => void;
};

export async function streamChat({
  sessionId,
  query,
  modelId,
  signal,
  onEvent,
}: StreamChatOptions): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/chat/completions`, {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify({
      session_id: sessionId,
      query,
      ...(modelId ? { model: modelId } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP ${response.status}: ${body}`);
  }
  if (!response.body) {
    throw new Error("SSE response has no readable body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const { blocks, rest } = splitSseBuffer(buffer);
    buffer = rest;

    for (const block of blocks) {
      for (const event of parseSseBlock(block)) {
        onEvent(event);
      }
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    for (const event of parseSseBlock(buffer)) {
      onEvent(event);
    }
  }
}
