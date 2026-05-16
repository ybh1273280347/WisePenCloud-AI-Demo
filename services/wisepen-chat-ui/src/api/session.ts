import type { AssistantPart, ChatMessage, ChatSession } from "../types/chat";
import { apiFetch, ensureApiOk, readApiData, readJson } from "./client";

type SessionDto = {
  id: string;
  user_id?: string;
  title: string;
  created_at: string;
  updated_at: string;
  is_pinned?: boolean;
  pinned_at?: string | null;
};

export class ApiStatusError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiStatusError";
    this.status = status;
  }
}

export async function createSession(title = "新对话"): Promise<string> {
  const response = await apiFetch("/chat/session/createSession", {
    method: "POST",
    body: JSON.stringify({ title }),
  });

  const data = await readApiData<SessionDto>(response, "创建对话失败");
  return data.id;
}

export async function listSessions(page = 1, size = 50): Promise<ChatSession[]> {
  const params = new URLSearchParams({
    page: String(page),
    size: String(size),
  });
  const response = await apiFetch(`/chat/session/listSessions?${params.toString()}`);
  const data = await readApiData<{ list?: SessionDto[] }>(response, "加载对话列表失败");
  return (data.list || []).map(mapSession);
}

export async function deleteSession(sessionId: string): Promise<void> {
  if (!sessionId) {
    return;
  }

  const response = await apiFetch(`/chat/session/deleteSession?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  await ensureApiOk(response, "删除对话失败");
}

export async function rollbackSessionToMessage(sessionId: string, messageId: string): Promise<void> {
  const response = await apiFetch(`/chat/session/rollbackToMessage?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId }),
  });
  let body: { code?: number; msg?: string; message?: string; detail?: string };
  try {
    body = await readJson(response);
  } catch {
    body = {};
  }
  if (!response.ok || body.code !== 200) {
    const backendMessage = body.msg || body.message || body.detail;
    const message =
      response.status === 404
        ? backendMessage === "message not found"
          ? "找不到要回滚的消息，正在尝试刷新历史后重试。"
          : "后端还没有加载回滚接口，请重启 wisepen-chat-service 后再试。"
        : backendMessage || `回滚对话失败. HTTP ${response.status}`;
    throw new ApiStatusError(message, response.status);
  }
}

export async function renameSession(sessionId: string, newTitle: string): Promise<ChatSession> {
  const response = await apiFetch(`/chat/session/renameSession?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: JSON.stringify({ new_title: newTitle }),
  });
  const data = await readApiData<SessionDto>(response, "重命名对话失败");
  return mapSession(data);
}

export async function pinSession(sessionId: string, setPin: boolean): Promise<ChatSession> {
  const response = await apiFetch(`/chat/session/pinSession?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    body: JSON.stringify({ set_pin: setPin }),
  });
  const data = await readApiData<SessionDto>(response, "更新置顶状态失败");
  return mapSession(data);
}

export async function listHistoryMessages(sessionId: string, page = 1, size = 50): Promise<ChatMessage[]> {
  const params = new URLSearchParams({
    session_id: sessionId,
    page: String(page),
    size: String(size),
  });
  const response = await apiFetch(`/chat/session/listHistoryMessages?${params.toString()}`);
  const data = await readApiData<{ list?: UIMessageDto[] }>(response, "加载历史消息失败");
  return (data.list || []).map(mapUiMessage);
}

function mapSession(dto: SessionDto): ChatSession {
  return {
    id: dto.id,
    userId: dto.user_id || "",
    title: dto.title || "新对话",
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
    isPinned: dto.is_pinned,
  };
}

type UIMessagePartDto = {
  type: string;
  text?: string;
  state?: string;
  toolCallId?: string;
  input?: unknown;
  output?: unknown;
};

type UIMessageDto = {
  id: string;
  role: string;
  parts: UIMessagePartDto[];
  createdAt?: string;
};

function mapUiMessage(dto: UIMessageDto): ChatMessage {
  const createdAt = dto.createdAt ? Date.parse(dto.createdAt) || Date.now() : Date.now();
  if (dto.role === "user") {
    return {
      id: dto.id || crypto.randomUUID(),
      role: "user",
      content: stripAttachmentBlock(dto.parts
        .filter((part) => part.type === "text")
        .map((part) => part.text || "")
        .join("\n")),
      createdAt,
    };
  }

  return {
    id: dto.id || crypto.randomUUID(),
    role: "assistant",
    parts: dto.parts.flatMap((part, index) => mapAssistantPart(part, index)),
    createdAt,
    status: "completed",
  };
}

function mapAssistantPart(part: UIMessagePartDto, index: number): AssistantPart[] {
  if (part.type === "text" && part.text) {
    return [{ type: "text", id: `history_text_${index}_${crypto.randomUUID()}`, content: part.text }];
  }

  if (part.type.startsWith("tool-")) {
    const output = stringifyOutput(part.output);
    return [
      {
        type: "tool_call",
        id: `history_tool_${index}_${crypto.randomUUID()}`,
        callId: part.toolCallId || `history_call_${index}`,
        toolName: part.type.slice("tool-".length) || "tool",
        status: output.includes("[Tool Error]") ? "error" : "completed",
        input: part.input,
        output,
        startedAt: Date.now(),
        completedAt: Date.now(),
      },
    ];
  }

  return [];
}

function stringifyOutput(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value == null) {
    return "";
  }
  return JSON.stringify(value, null, 2);
}

function stripAttachmentBlock(text: string): string {
  return text.replace(/\n\n\[Attached files\][\s\S]*$/m, "");
}
