import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { deleteChatFile, extractGeneratedFilesFromToolOutput, uploadChatFile, } from "../api/file";
import { flattenModels, listModels } from "../api/model";
import {
  ApiStatusError,
  createSession,
  deleteSession,
  listHistoryMessages,
  listSessions,
  pinSession,
  renameSession,
  rollbackSessionToMessage,
} from "../api/session";
import { streamChat } from "../api/streamChat";
import type {
  AssistantMessage,
  AssistantPart,
  ChatFileAttachment,
  ChatFileItem,
  ChatMessage,
  ChatModel,
  ChatResourceRef,
  ChatSession,
  ConnectionStatus,
  ModelGroups,
  UserMessage,
} from "../types/chat";
import type { SseEvent } from "../types/sse";
import { asOutputText } from "../utils/safeJson";
import { ChatHeader } from "./ChatHeader";
import { ChatInput } from "./ChatInput";
import { FilePreviewPanel } from "./FilePreviewPanel";
import { MessageList } from "./MessageList";
import { Sidebar } from "./Sidebar";

function id(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function appendTextPart(message: AssistantMessage, delta: string): AssistantMessage {
  const parts = [...message.parts];
  const last = parts[parts.length - 1];

  if (last?.type === "text") {
    parts[parts.length - 1] = { ...last, content: last.content + delta };
  } else {
    parts.push({ type: "text", id: id("part"), content: delta });
  }

  return { ...message, parts };
}

function updateToolPart(
  message: AssistantMessage,
  callId: string,
  updater: (part: Extract<AssistantPart, { type: "tool_call" }>) => AssistantPart,
): AssistantMessage {
  return {
    ...message,
    parts: message.parts.map((part) => {
      if (part.type === "tool_call" && part.callId === callId) {
        return updater(part);
      }
      return part;
    }),
  };
}

function createToolPart(event: SseEvent): AssistantPart {
  return {
    type: "tool_call",
    id: id("tool"),
    callId: String(event.toolCallId || id("call")),
    toolName: String(event.toolName || "tool"),
    status: "running",
    startedAt: Date.now(),
  };
}

function defaultModelId(models: ChatModel[]): number | null {
  return models.find((model) => model.isDefault)?.id ?? models[0]?.id ?? null;
}

function mergeFileItems(current: ChatFileItem[], incoming: ChatFileItem[]): ChatFileItem[] {
  if (incoming.length === 0) {
    return current;
  }

  const byId = new Map(current.map((file) => [file.id, file]));
  for (const file of incoming) {
    byId.set(file.id, file);
  }
  return Array.from(byId.values()).sort((left, right) => left.createdAt - right.createdAt);
}

function extractGeneratedFilesFromMessages(messages: ChatMessage[]): ChatFileItem[] {
  return messages.flatMap((message) => {
    if (message.role !== "assistant") {
      return [];
    }
    return message.parts.flatMap((part) =>
      part.type === "tool_call" && part.output
        ? extractGeneratedFilesFromToolOutput(part.output)
        : [],
    );
  });
}

function attachFileRefsToQuery(text: string, files: ChatFileItem[]): string {
  const attachedFiles = fileAttachmentsFromItems(files);
  if (attachedFiles.length === 0) {
    return text;
  }

  const fileLines = attachedFiles
    .map(
      (file) =>
        `- file_name: ${file.fileName}\n  file_ref: ${file.fileRef}\n  content_type: ${file.contentType}`,
    )
    .join("\n");
  return `${text}\n\n[Attached files]\n${fileLines}\nInstruction: If these files are relevant, call document_parse with the listed file_ref values.`;
}

function fileAttachmentsFromItems(files: ChatFileItem[]): ChatFileAttachment[] {
  return files
    .filter((file) => file.source === "upload" && file.fileRef)
    .map((file) => ({
      fileName: file.fileName,
      fileRef: file.fileRef || "",
      contentType: file.contentType,
      sizeBytes: file.sizeBytes,
    }));
}

function attachResourceRefsToQuery(text: string, resources: ChatResourceRef[]): string {
  if (resources.length === 0) {
    return text;
  }

  const resourceLines = resources
    .map(
      (resource) =>
        `- resource_id: ${resource.resourceId}\n  resource_type: ${resource.resourceType}\n  title: ${resource.title}`,
    )
    .join("\n");
  return `${text}\n\n[Attached resources]\n${resourceLines}\nInstruction: If these resources are relevant, use their resource_id as source_ref.`;
}

function filesForMessages(uploadedFiles: ChatFileItem[], messages: ChatMessage[]): ChatFileItem[] {
  return mergeFileItems(uploadedFiles, extractGeneratedFilesFromMessages(messages));
}

async function loadSessionState(nextSessionId: string): Promise<{
  history: ChatMessage[];
  nextFiles: ChatFileItem[];
}> {
  const [history, uploadedFiles] = await Promise.all([
    listHistoryMessages(nextSessionId),
    Promise.resolve([] as ChatFileItem[]),
  ]);
  return {
    history,
    nextFiles: filesForMessages(uploadedFiles, history),
  };
}

type SendMessageOptions = {
  baseMessages?: ChatMessage[];
  baseFiles?: ChatFileItem[];
};

function findMatchingUserMessage(
  history: ChatMessage[],
  target: UserMessage,
): UserMessage | null {
  const byId = history.find(
    (message): message is UserMessage => message.role === "user" && message.id === target.id,
  );
  if (byId) {
    return byId;
  }

  const sameContent = history.filter(
    (message): message is UserMessage =>
      message.role === "user" && message.content === target.content,
  );
  if (sameContent.length === 0) {
    return null;
  }

  return sameContent.reduce((best, current) => {
    const bestDistance = Math.abs(best.createdAt - target.createdAt);
    const currentDistance = Math.abs(current.createdAt - target.createdAt);
    return currentDistance < bestDistance ? current : best;
  });
}

function messagesBeforeUserMessage(
  currentMessages: ChatMessage[],
  target: UserMessage,
): ChatMessage[] | null {
  const targetIndex = currentMessages.findIndex(
    (message) => message.role === "user" && message.id === target.id,
  );
  if (targetIndex >= 0) {
    return currentMessages.slice(0, targetIndex);
  }

  const matchingIndex = currentMessages.findIndex(
    (message) =>
      message.role === "user" &&
      message.content === target.content &&
      message.createdAt === target.createdAt,
  );
  if (matchingIndex >= 0) {
    return currentMessages.slice(0, matchingIndex);
  }

  return null;
}

export function ChatPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [modelGroups, setModelGroups] = useState<ModelGroups>({
    standard: [],
    advanced: [],
    other: [],
  });
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [files, setFiles] = useState<ChatFileItem[]>([]);
  const [resourceRefs, setResourceRefs] = useState<ChatResourceRef[]>([]);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [inputDraft, setInputDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const models = useMemo(() => flattenModels(modelGroups), [modelGroups]);
  const selectedModelName = models.find((model) => model.id === selectedModelId)?.name;
  const currentSession = sessions.find((session) => session.id === sessionId);
  const streaming = messages.some(
    (message) => message.role === "assistant" && message.status === "streaming",
  );

  const refreshSessions = useCallback(async (): Promise<ChatSession[]> => {
    const nextSessions = await listSessions();
    setSessions(nextSessions);
    return nextSessions;
  }, []);

  const loadSession = useCallback(
    async (nextSessionId: string) => {
      if (streaming) {
        return;
      }

      setConnectionStatus("connecting");
      setErrorText(null);
      setLoadingHistory(true);
      try {
        const { history, nextFiles } = await loadSessionState(nextSessionId);
        setSessionId(nextSessionId);
        setMessages(history);
        setFiles(nextFiles);
        setSelectedFileId(null);
        setInputDraft("");
        setConnectionStatus("connected");
      } catch (error) {
        setConnectionStatus("error");
        setErrorText(error instanceof Error ? error.message : String(error));
      } finally {
        setLoadingHistory(false);
      }
    },
    [streaming],
  );

  const createAndOpenSession = useCallback(async () => {
    setConnectionStatus("connecting");
    setErrorText(null);
    try {
      const nextSessionId = await createSession("新对话");
      await refreshSessions();
      setSessionId(nextSessionId);
      setMessages([]);
      setFiles([]);
      setSelectedFileId(null);
      setInputDraft("");
      setConnectionStatus("connected");
    } catch (error) {
      setSessionId(null);
      setConnectionStatus("error");
      setErrorText(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }, [refreshSessions]);

  const bootstrap = useCallback(async () => {
    setConnectionStatus("connecting");
    setErrorText(null);
    try {
      const [nextModelGroups, nextSessions] = await Promise.all([
        listModels(),
        listSessions(),
      ]);
      setModelGroups(nextModelGroups);

      const nextModels = flattenModels(nextModelGroups);
      setSelectedModelId((current) => current ?? defaultModelId(nextModels));
      setSessions(nextSessions);

      if (nextSessions.length > 0) {
        const firstSessionId = nextSessions[0].id;
        const { history, nextFiles } = await loadSessionState(firstSessionId);
        setSessionId(firstSessionId);
        setMessages(history);
        setFiles(nextFiles);
        setSelectedFileId(null);
        setInputDraft("");
      } else {
        const nextSessionId = await createSession("新对话");
        setSessionId(nextSessionId);
        setMessages([]);
        setFiles([]);
        setSelectedFileId(null);
        setInputDraft("");
        await refreshSessions();
      }

      setConnectionStatus("connected");
    } catch (error) {
      setSessionId(null);
      setConnectionStatus("error");
      setErrorText(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }, [refreshSessions]);

  useEffect(() => {
    bootstrap().catch((error) => console.error(error));

    return () => {
      abortRef.current?.abort();
    };
  }, [bootstrap]);

  const patchAssistant = useCallback((assistantId: string, patcher: (message: AssistantMessage) => AssistantMessage) => {
    setMessages((current) =>
      current.map((message) => {
        if (message.role === "assistant" && message.id === assistantId) {
          return patcher(message);
        }
        return message;
      }),
    );
  }, []);

  const addFiles = useCallback((incoming: ChatFileItem[]) => {
    if (incoming.length === 0) {
      return;
    }
    setFiles((current) => mergeFileItems(current, incoming));
  }, []);

  const refreshCurrentMessages = useCallback(async (targetSessionId: string) => {
    const { history, nextFiles } = await loadSessionState(targetSessionId);
    setMessages(history);
    setFiles(nextFiles);
    setSelectedFileId((current) =>
      current && nextFiles.some((file) => file.id === current) ? current : null,
    );
    return { history, nextFiles };
  }, []);

  const handleSseEvent = useCallback(
    (assistantId: string, event: SseEvent) => {
      if (event._raw === "[DONE]") {
        patchAssistant(assistantId, (message) => ({ ...message, status: "completed" }));
        return;
      }

      if (event._raw) {
        console.warn("Unhandled raw SSE event:", event._raw);
        return;
      }

      if (!event.type) {
        console.warn("SSE event without type:", event);
        return;
      }

      switch (event.type) {
        case "start": {
          break;
        }
        case "text-start": {
          break;
        }
        case "text-delta": {
          const delta = typeof event.delta === "string" ? event.delta : "";
          if (delta) {
            patchAssistant(assistantId, (message) => appendTextPart(message, delta));
          }
          break;
        }
        case "text-end": {
          break;
        }
        case "reasoning-start": {
          break;
        }
        case "reasoning-delta": {
          const delta = typeof event.delta === "string" ? event.delta : "";
          if (delta) {
            patchAssistant(assistantId, (message) => appendTextPart(message, delta));
          }
          break;
        }
        case "reasoning-end": {
          break;
        }
        case "tool-input-start": {
          const toolPart = createToolPart(event);
          patchAssistant(assistantId, (message) => ({
            ...message,
            parts: [...message.parts, toolPart],
          }));
          break;
        }
        case "tool-input-available": {
          const callId = String(event.toolCallId || "");
          if (!callId) {
            console.warn("tool-input-available missing toolCallId:", event);
            break;
          }
          patchAssistant(assistantId, (message) =>
            updateToolPart(message, callId, (part) => ({
              ...part,
              input: event.input,
            })),
          );
          break;
        }
        case "tool-output-available": {
          const callId = String(event.toolCallId || "");
          if (!callId) {
            console.warn("tool-output-available missing toolCallId:", event);
            break;
          }
          const output = asOutputText(event.output);
          addFiles(extractGeneratedFilesFromToolOutput(output));
          patchAssistant(assistantId, (message) =>
            updateToolPart(message, callId, (part) => ({
              ...part,
              output,
              status: output.includes("[Tool Error]") ? "error" : "completed",
              completedAt: Date.now(),
            })),
          );
          break;
        }
        case "start-step": {
          break;
        }
        case "finish-step": {
          break;
        }
        case "source-url": {
          break;
        }
        case "source-urls": {
          break;
        }
        case "error": {
          const streamErrorText = typeof event.errorText === "string" ? event.errorText : "Stream error.";
          patchAssistant(assistantId, (message) => ({
            ...appendTextPart(message, `\n\n[Error] ${streamErrorText}`),
            status: "error",
          }));
          break;
        }
        case "abort": {
          const reason = typeof event.reason === "string" ? event.reason : "Aborted.";
          patchAssistant(assistantId, (message) => ({
            ...appendTextPart(message, `\n\n[Aborted] ${reason}`),
            status: "stopped",
          }));
          break;
        }
        case "finish": {
          patchAssistant(assistantId, (message) =>
            message.status === "streaming" ? { ...message, status: "completed" } : message,
          );
          break;
        }
        default: {
          console.warn("Unknown SSE event type:", event.type, event);
        }
      }
    },
    [addFiles, patchAssistant],
  );

  const sendMessage = useCallback(
    async (text: string, options: SendMessageOptions = {}) => {
      if (!sessionId || streaming) {
        return;
      }

      const assistantId = id("assistant");
      const controller = new AbortController();
      const messageFiles = options.baseFiles ?? files;
      const fileAttachments = fileAttachmentsFromItems(messageFiles);
      const messageResourceRefs = resourceRefs;
      const outgoingQuery = attachResourceRefsToQuery(
        attachFileRefsToQuery(text, messageFiles),
        messageResourceRefs,
      );
      abortRef.current = controller;

      if (options.baseFiles) {
        setFiles(options.baseFiles);
      }

      setMessages((current) => {
        const baseMessages = options.baseMessages ?? current;
        return [
          ...baseMessages,
          {
            id: id("user"),
            role: "user",
            content: text,
            attachments: fileAttachments,
            createdAt: Date.now(),
          },
          {
            id: assistantId,
            role: "assistant",
            parts: [],
            createdAt: Date.now(),
            status: "streaming",
          },
        ];
      });
      setFiles((current) => current.filter((file) => file.source !== "upload"));
      setSelectedFileId((current) => {
        const selectedFile = messageFiles.find((file) => file.id === current);
        return selectedFile?.source === "upload" ? null : current;
      });

      try {
        await streamChat({
          sessionId,
          query: outgoingQuery,
          modelId: selectedModelId,
          fileAttachments,
          resourceRefs: messageResourceRefs,
          signal: controller.signal,
          onEvent: (event) => handleSseEvent(assistantId, event),
        });
        setResourceRefs([]);
        patchAssistant(assistantId, (message) =>
          message.status === "streaming" ? { ...message, status: "completed" } : message,
        );
        setConnectionStatus("connected");
        await refreshSessions().catch(() => undefined);
      } catch (error) {
        if (controller.signal.aborted) {
          patchAssistant(assistantId, (message) => ({ ...message, status: "stopped" }));
        } else {
          setConnectionStatus("error");
          setErrorText(error instanceof Error ? error.message : String(error));
          patchAssistant(assistantId, (message) => ({
            ...appendTextPart(
              message,
              `\n\n[Error] ${error instanceof Error ? error.message : String(error)}`,
            ),
            status: "error",
          }));
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [
      files,
      handleSseEvent,
      patchAssistant,
      refreshCurrentMessages,
      refreshSessions,
      resourceRefs,
      selectedModelId,
      sessionId,
      streaming,
    ],
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const rollbackConversationToMessage = useCallback(
    async (message: UserMessage): Promise<boolean> => {
      if (!sessionId || streaming) {
        return false;
      }

      setConnectionStatus("connecting");
      setErrorText(null);
      try {
        let rollbackTarget = message;

        if (message.id.startsWith("user_")) {
          const { history } = await loadSessionState(sessionId);
          const matchedTarget = findMatchingUserMessage(history, message);
          if (!matchedTarget) {
            throw new ApiStatusError("找不到要回滚的消息，请刷新历史后重试。", 404);
          }
          rollbackTarget = matchedTarget;
        }

        try {
          await rollbackSessionToMessage(sessionId, rollbackTarget.id);
        } catch (error) {
          if (!(error instanceof ApiStatusError) || error.status !== 404) {
            throw error;
          }

          const { history } = await loadSessionState(sessionId);
          const refreshedTarget = findMatchingUserMessage(history, message);
          if (!refreshedTarget || refreshedTarget.id === rollbackTarget.id) {
            throw error;
          }
          await rollbackSessionToMessage(sessionId, refreshedTarget.id);
        }

        await refreshSessions().catch(() => undefined);
        setConnectionStatus("connected");
        return true;
      } catch (error) {
        setConnectionStatus("error");
        setErrorText(
          error instanceof ApiStatusError && error.status === 404
            ? error.message
            : error instanceof Error
              ? error.message
              : String(error),
        );
        return false;
      }
    },
    [refreshSessions, sessionId, streaming],
  );

  const editUserMessage = useCallback(
    async (message: UserMessage, nextContent: string) => {
      const text = nextContent.trim();
      if (!text) {
        return false;
      }

      const previousMessages = messages;
      const previousFiles = files;
      const baseMessages = messagesBeforeUserMessage(messages, message);
      if (!baseMessages) {
        setErrorText("找不到要编辑的消息，请刷新历史后重试。");
        return false;
      }
      const baseFiles = filesForMessages(
        previousFiles.filter((file) => file.source === "upload"),
        baseMessages,
      );

      setMessages(baseMessages);
      setFiles(baseFiles);
      setSelectedFileId((current) =>
        current && baseFiles.some((file) => file.id === current) ? current : null,
      );

      const rolledBack = await rollbackConversationToMessage(message);
      if (!rolledBack) {
        setMessages(previousMessages);
        setFiles(previousFiles);
        setSelectedFileId((current) =>
          current && previousFiles.some((file) => file.id === current) ? current : null,
        );
        return false;
      }

      setInputDraft("");
      void sendMessage(text, {
        baseMessages,
        baseFiles,
      });
      return true;
    },
    [files, messages, rollbackConversationToMessage, sendMessage],
  );

  const regenerateAssistantMessage = useCallback(
    async (_message: AssistantMessage, previousUserMessage: UserMessage) => {
      const text = previousUserMessage.content;
      const previousMessages = messages;
      const previousFiles = files;
      const baseMessages = messagesBeforeUserMessage(messages, previousUserMessage);
      if (!baseMessages) {
        setErrorText("找不到要重新输出的消息，请刷新历史后重试。");
        return;
      }
      const baseFiles = filesForMessages(
        previousFiles.filter((file) => file.source === "upload"),
        baseMessages,
      );

      setMessages(baseMessages);
      setFiles(baseFiles);
      setSelectedFileId((current) =>
        current && baseFiles.some((file) => file.id === current) ? current : null,
      );

      const rolledBack = await rollbackConversationToMessage(previousUserMessage);
      if (!rolledBack) {
        setMessages(previousMessages);
        setFiles(previousFiles);
        setSelectedFileId((current) =>
          current && previousFiles.some((file) => file.id === current) ? current : null,
        );
        return;
      }

      setInputDraft("");
      void sendMessage(text, {
        baseMessages,
        baseFiles,
      });
    },
    [files, messages, rollbackConversationToMessage, sendMessage],
  );

  const newChat = useCallback(async () => {
    if (streaming) {
      return;
    }
    await createAndOpenSession();
  }, [createAndOpenSession, streaming]);

  const deleteSessionById = useCallback(async (targetSessionId: string) => {
    if (streaming) {
      return;
    }
    setConnectionStatus("connecting");
    setErrorText(null);
    try {
      await deleteSession(targetSessionId);
      const nextSessions = await refreshSessions();
      if (targetSessionId === sessionId) {
        if (nextSessions.length > 0) {
          await loadSession(nextSessions[0].id);
        } else {
          await createAndOpenSession();
        }
      } else {
        setConnectionStatus("connected");
      }
    } catch (error) {
      setConnectionStatus("error");
      setErrorText(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }, [createAndOpenSession, loadSession, refreshSessions, sessionId, streaming]);

  const handleRenameSession = useCallback(
    async (targetSessionId: string, title: string) => {
      setConnectionStatus("connecting");
      setErrorText(null);
      try {
        const renamed = await renameSession(targetSessionId, title);
        setSessions((current) =>
          current.map((item) => (item.id === targetSessionId ? { ...item, ...renamed } : item)),
        );
        setConnectionStatus("connected");
      } catch (error) {
        setConnectionStatus("error");
        setErrorText(error instanceof Error ? error.message : String(error));
        throw error;
      }
    },
    [],
  );

  const handlePinSession = useCallback(
    async (targetSessionId: string, setPin: boolean) => {
      setConnectionStatus("connecting");
      setErrorText(null);
      try {
        const pinned = await pinSession(targetSessionId, setPin);
        setSessions((current) =>
          current.map((item) => (item.id === targetSessionId ? { ...item, ...pinned } : item)),
        );
        await refreshSessions();
        setConnectionStatus("connected");
      } catch (error) {
        setConnectionStatus("error");
        setErrorText(error instanceof Error ? error.message : String(error));
        throw error;
      }
    },
    [refreshSessions],
  );

  const handleUploadFile = useCallback(
    async (file: File) => {
      if (!sessionId) {
        return;
      }
      const uploaded = await uploadChatFile(sessionId, file);
      setFiles((current) => mergeFileItems(current, [uploaded]));
    },
    [sessionId],
  );

  const handleDeleteFile = useCallback(
    async (file: ChatFileItem) => {
      if (!sessionId || file.source !== "upload") {
        return;
      }
      await deleteChatFile(sessionId, file);
      setFiles((current) => current.filter((item) => item.id !== file.id));
      setSelectedFileId((current) => (current === file.id ? null : current));
    },
    [sessionId],
  );

  const handleAttachResource = useCallback((resource: ChatResourceRef) => {
    setResourceRefs((current) => {
      const exists = current.some((item) => item.resourceId === resource.resourceId);
      if (exists) {
        return current.map((item) => (item.resourceId === resource.resourceId ? resource : item));
      }
      return [...current, resource];
    });
  }, []);

  const handleRemoveResource = useCallback((resourceId: string) => {
    setResourceRefs((current) => current.filter((item) => item.resourceId !== resourceId));
  }, []);

  return (
    <div className="app-shell">
      <div className="app-workbench">
        <Sidebar
          currentId={sessionId}
          items={sessions}
          streaming={streaming}
          loadingHistory={loadingHistory}
          onNewChat={newChat}
          onSelect={loadSession}
          onRename={handleRenameSession}
          onDelete={deleteSessionById}
          onPin={handlePinSession}
        />
        <main className="chat-main">
          <ChatHeader
            title={currentSession?.title || "新对话"}
            modelName={selectedModelName}
            modelGroups={modelGroups}
            selectedModelId={selectedModelId}
            sessionId={sessionId}
            onModelChange={setSelectedModelId}
            connectionStatus={connectionStatus}
            streaming={streaming}
            fileCount={files.length}
            messageCount={messages.length}
          />
          <div className="chat-body">
            {errorText ? <div className="chat-error-banner">{errorText}</div> : null}
            <MessageList
              messages={messages}
              loading={loadingHistory}
              actionDisabled={streaming || loadingHistory || connectionStatus === "connecting"}
              onEditUserMessage={editUserMessage}
              onRegenerateAssistantMessage={regenerateAssistantMessage}
            />
          </div>
        </main>
      </div>
      {selectedFileId && files.some((file) => file.id === selectedFileId) ? (
        <FilePreviewPanel
          files={files}
          selectedFileId={selectedFileId}
          onSelectFile={setSelectedFileId}
          onDelete={handleDeleteFile}
          onClose={() => setSelectedFileId(null)}
        />
      ) : null}
      <ChatInput
        disabled={!sessionId || connectionStatus === "connecting" || loadingHistory}
        streaming={streaming}
        selectedModelName={selectedModelName}
        files={files}
        resourceRefs={resourceRefs}
        value={inputDraft}
        onValueChange={setInputDraft}
        onSend={sendMessage}
        onStop={stopStreaming}
        onUploadFile={handleUploadFile}
        onPreviewFile={setSelectedFileId}
        onAttachResource={handleAttachResource}
        onRemoveResource={handleRemoveResource}
      />
    </div>
  );
}
