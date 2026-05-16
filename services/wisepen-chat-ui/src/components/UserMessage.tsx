import { KeyboardEvent, useEffect, useRef, useState } from "react";
import type { UserMessage as UserMessageType } from "../types/chat";
import { copyTextToClipboard } from "../utils/clipboard";
import { MessageActionButton } from "./MessageActionButton";
import { MarkdownContent } from "./MarkdownContent";

type UserMessageProps = {
  message: UserMessageType;
  actionDisabled?: boolean;
  onEdit?: (message: UserMessageType, nextContent: string) => Promise<boolean> | boolean | void;
};

export function UserMessage({ message, actionDisabled = false, onEdit }: UserMessageProps) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!editing) {
      return;
    }
    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) {
        return;
      }
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    });
  }, [editing]);

  async function copyMessage() {
    await copyTextToClipboard(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  async function submitEdit() {
    const nextContent = draft.trim();
    if (!nextContent || saving) {
      return;
    }

    setSaving(true);
    try {
      const ok = await onEdit?.(message, nextContent);
      if (ok !== false) {
        setEditing(false);
      }
    } finally {
      setSaving(false);
    }
  }

  function cancelEdit() {
    setDraft(message.content);
    setEditing(false);
  }

  function handleEditKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitEdit();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  return (
    <article className="message message-user">
      <div className="user-message-stack">
        {editing ? (
          <>
            <div className="message-bubble user-bubble user-edit-bubble">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleEditKeyDown}
                disabled={saving || actionDisabled}
                rows={Math.max(3, Math.min(10, draft.split("\n").length + 1))}
              />
            </div>
            <div className="user-edit-actions">
              <button type="button" className="user-edit-cancel" onClick={cancelEdit} disabled={saving}>
                取消
              </button>
              <button
                type="button"
                className="user-edit-submit"
                onClick={submitEdit}
                disabled={saving || actionDisabled || !draft.trim()}
              >
                发送
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="message-bubble user-bubble">
              <MarkdownContent content={message.content} className="user-markdown" />
            </div>
            <div className="message-actions user-message-actions">
              <MessageActionButton
                icon="copy"
                title={copied ? "已复制" : "复制"}
                aria-label="复制用户消息"
                active={copied}
                feedback={copied ? "√" : undefined}
                onClick={copyMessage}
              />
              <MessageActionButton
                icon="edit"
                title="重新编辑"
                aria-label="重新编辑"
                disabled={actionDisabled}
                onClick={() => {
                  setDraft(message.content);
                  setEditing(true);
                }}
              />
            </div>
          </>
        )}
      </div>
    </article>
  );
}
