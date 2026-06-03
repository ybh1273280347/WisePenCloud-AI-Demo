import {apiBaseUrl, makeHeaders} from "./client";
import {parseSseBlock, splitSseBuffer} from "./sseParser";
import type {ChatFileAttachment, ChatResourceRef} from "../types/chat";
import type {SseEvent} from "../types/sse";

type StreamChatOptions = {
  sessionId: string;
  query: string;
  modelId?: number | null;
  fileAttachments?: ChatFileAttachment[];
  resourceRefs?: ChatResourceRef[];
  signal: AbortSignal;
  onEvent: (event: SseEvent) => void;
};

export async function streamChat({
  sessionId,
  query,
  modelId,
  fileAttachments = [],
  resourceRefs = [],
  signal,
  onEvent,
}: StreamChatOptions): Promise<void> {
  const fileStates = fileAttachments.map((file) => ({
    key: "file_ref",
    value: JSON.stringify({
      file_ref: file.fileRef,
      file_name: file.fileName,
      content_type: file.contentType,
      size_bytes: file.sizeBytes,
      next_step: "Call document_parse with this file_ref when the user asks about this file.",
    }),
  }));

  const resourceStates = resourceRefs.map((resource) => ({
    key: "resource_ref",
    value: JSON.stringify({
      resource_id: resource.resourceId,
      resource_type: resource.resourceType,
      title: resource.title,
      source: resource.source,
    }),
  }));
  const states = [...fileStates, ...resourceStates];

  const response = await fetch(`${apiBaseUrl}/chat/completions`, {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify({
      session_id: sessionId,
      query,
      ...(modelId ? { model: modelId } : {}),
      ...(states.length > 0 ? { states } : {}),
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
