import type { MemoryItem } from "../types/chat";
import { apiFetch, ensureApiOk, readApiData } from "./client";

type MemoryDto = {
  id: string;
  memory: string;
  metadata?: Record<string, unknown>;
};

export async function listMemories(): Promise<MemoryItem[]> {
  const response = await apiFetch("/chat/memory/listMemories");
  const data = await readApiData<MemoryDto[]>(response, "Failed to list memories");
  return data.map((item) => ({
    id: item.id,
    memory: item.memory,
    metadata: item.metadata || {},
  }));
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const response = await apiFetch(`/chat/memory/deleteMemory?memory_id=${encodeURIComponent(memoryId)}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  await ensureApiOk(response, "Failed to delete memory");
}

export async function deleteAllMemories(): Promise<void> {
  const response = await apiFetch("/chat/memory/deleteAllMemories", {
    method: "DELETE",
  });
  await ensureApiOk(response, "Failed to delete all memories");
}
