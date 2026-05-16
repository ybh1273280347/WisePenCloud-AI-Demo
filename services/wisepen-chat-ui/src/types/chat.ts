export type ChatMessage =
  | {
      id: string;
      role: "user";
      content: string;
      createdAt: number;
    }
  | {
      id: string;
      role: "assistant";
      parts: AssistantPart[];
      createdAt: number;
      status: "streaming" | "completed" | "stopped" | "error";
    };

export type AssistantPart =
  | {
      type: "text";
      id: string;
      content: string;
    }
  | {
      type: "tool_call";
      id: string;
      callId: string;
      toolName: string;
      status: "running" | "completed" | "error";
      input?: unknown;
      output?: string;
      startedAt: number;
      completedAt?: number;
    };

export type AssistantMessage = Extract<ChatMessage, { role: "assistant" }>;
export type UserMessage = Extract<ChatMessage, { role: "user" }>;

export type ConnectionStatus = "connecting" | "connected" | "offline" | "error";

export type ChatSession = {
  id: string;
  userId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  isPinned?: boolean;
};

export type ChatModel = {
  id: number;
  name: string;
  vendor: string;
  type: string;
  ratio: number;
  supportThinking: boolean;
  supportVision: boolean;
  isDefault: boolean;
};

export type ModelGroups = {
  standard: ChatModel[];
  advanced: ChatModel[];
  other: ChatModel[];
};

export type MemoryItem = {
  id: string;
  memory: string;
  metadata: Record<string, unknown>;
};

export type ChatFileSource = "upload" | "generated";

export type ChatFileItem = {
  id: string;
  source: ChatFileSource;
  fileName: string;
  contentType: string;
  previewUrl: string;
  downloadUrl: string;
  createdAt: number;
  sizeBytes?: number;
  fileId?: string;
  fileRef?: string;
  downloadRef?: string;
};
