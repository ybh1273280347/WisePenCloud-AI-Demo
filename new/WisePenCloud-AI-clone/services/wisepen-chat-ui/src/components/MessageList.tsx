import type {
    AssistantMessage as AssistantMessageType,
    ChatMessage,
    UserMessage as UserMessageType
} from "../types/chat";
import {AssistantMessage} from "./AssistantMessage";
import {DailyMascot} from "./DailyMascot";
import {UserMessage} from "./UserMessage";

type MessageListProps = {
  messages: ChatMessage[];
  loading?: boolean;
  actionDisabled?: boolean;
  onEditUserMessage?: (
    message: UserMessageType,
    nextContent: string,
  ) => Promise<boolean> | boolean | void;
  onRegenerateAssistantMessage?: (
    message: AssistantMessageType,
    previousUserMessage: UserMessageType,
  ) => void;
};

export function MessageList({
  messages,
  loading = false,
  actionDisabled = false,
  onEditUserMessage,
  onRegenerateAssistantMessage,
}: MessageListProps) {
  if (loading) {
    return (
      <div className="empty-state">
        <p>正在加载历史消息...</p>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="empty-state">
        <DailyMascot />
        <p>今天想写点什么？</p>
      </div>
    );
  }

  let previousUserMessage: UserMessageType | null = null;

  return (
    <div className="message-list">
      {messages.map((message) => {
        if (message.role === "user") {
          previousUserMessage = message;
          return (
            <UserMessage
              key={message.id}
              message={message}
              actionDisabled={actionDisabled}
              onEdit={onEditUserMessage}
            />
          );
        }

        return (
          <AssistantMessage
            key={message.id}
            message={message}
            previousUserMessage={previousUserMessage}
            actionDisabled={actionDisabled}
            onRegenerate={onRegenerateAssistantMessage}
          />
        );
      })}
    </div>
  );
}
