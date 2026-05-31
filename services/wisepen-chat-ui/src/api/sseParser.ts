import type {SseEvent} from "../types/sse";

export function parseSseBlock(raw: string): SseEvent[] {
  const events: SseEvent[] = [];

  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }

    if (trimmed === "data: [DONE]") {
      events.push({ _raw: "[DONE]" });
      continue;
    }

    if (!trimmed.startsWith("data: ")) {
      continue;
    }

    const payload = trimmed.slice(6);
    try {
      events.push(JSON.parse(payload) as SseEvent);
    } catch {
      events.push({ _raw: payload });
    }
  }

  return events;
}

export function splitSseBuffer(buffer: string): { blocks: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks: string[] = [];
  let rest = normalized;

  while (rest.includes("\n\n")) {
    const index = rest.indexOf("\n\n");
    blocks.push(rest.slice(0, index));
    rest = rest.slice(index + 2);
  }

  return { blocks, rest };
}
