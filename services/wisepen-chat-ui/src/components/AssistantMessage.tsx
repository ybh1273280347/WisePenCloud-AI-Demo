import { useMemo, useState } from "react";
import type { AssistantMessage as AssistantMessageType } from "../types/chat";
import type { UserMessage as UserMessageType } from "../types/chat";
import { copyTextToClipboard } from "../utils/clipboard";
import { Badge } from "./Badge";
import { MarkdownContent } from "./MarkdownContent";
import { MessageActionButton } from "./MessageActionButton";
import { ToolTraceCard } from "./ToolTraceCard";

type AssistantMessageProps = {
  message: AssistantMessageType;
  previousUserMessage?: UserMessageType | null;
  actionDisabled?: boolean;
  onRegenerate?: (message: AssistantMessageType, previousUserMessage: UserMessageType) => void;
};

export function AssistantMessage({
  message,
  previousUserMessage,
  actionDisabled = false,
  onRegenerate,
}: AssistantMessageProps) {
  const [copied, setCopied] = useState(false);
  const outputText = useMemo(
    () =>
      message.parts
        .filter((part) => part.type === "text" && part.content.trim())
        .map((part) => (part.type === "text" ? part.content.trim() : ""))
        .join("\n\n"),
    [message.parts],
  );

  async function copyOutput() {
    if (!outputText) {
      return;
    }
    await copyTextToClipboard(outputText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <article className="message message-assistant">
      <div className="assistant-shell">
        {message.parts.map((part) => {
          if (part.type === "tool_call") {
            return <ToolTraceCard key={part.id} part={part} />;
          }

          if (!part.content) {
            return null;
          }

          return (
            <MarkdownContent key={part.id} content={part.content} className="assistant-text" />
          );
        })}

        {message.status !== "completed" && (
          <div className="assistant-status">
            <Badge status={message.status === "error" ? "error" : message.status} />
          </div>
        )}

        <div className="message-actions assistant-message-actions">
          <MessageActionButton
            icon="copy"
            title={copied ? "已复制" : "复制"}
            aria-label="复制模型输出"
            active={copied}
            feedback={copied ? "√" : undefined}
            disabled={!outputText}
            onClick={copyOutput}
          />
          <MessageActionButton
            icon="regenerate"
            title="重新输出"
            aria-label="重新输出"
            disabled={actionDisabled || message.status === "streaming" || !previousUserMessage}
            onClick={() => {
              if (previousUserMessage) {
                onRegenerate?.(message, previousUserMessage);
              }
            }}
          />
        </div>
      </div>
    </article>
  );
}
