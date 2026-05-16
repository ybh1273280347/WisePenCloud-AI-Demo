import { ChangeEvent, KeyboardEvent, useRef, useState } from "react";
import type { ChatFileItem } from "../types/chat";

type ChatInputProps = {
  disabled: boolean;
  streaming: boolean;
  selectedModelName?: string;
  files?: ChatFileItem[];
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onUploadFile?: (file: File) => Promise<void>;
  onPreviewFile?: (fileId: string) => void;
};

export function ChatInput({
  disabled,
  streaming,
  selectedModelName,
  files = [],
  value,
  onValueChange,
  onSend,
  onStop,
  onUploadFile,
  onPreviewFile,
}: ChatInputProps) {
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function submit() {
    const text = value.trim();
    if (!text || disabled || streaming) {
      return;
    }
    onValueChange("");
    onSend(text);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (selectedFiles.length === 0 || !onUploadFile || disabled || streaming) {
      return;
    }

    setUploading(true);
    try {
      for (const file of selectedFiles) {
        await onUploadFile(file);
      }
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="chat-input-wrap">
      <div className="chat-input-bar">
        <input
          ref={fileInputRef}
          className="composer-file-input"
          type="file"
          multiple
          onChange={handleFileChange}
          disabled={disabled || streaming || uploading}
        />
        <textarea
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? "正在准备对话..." : "输入消息..."}
          disabled={disabled || streaming}
          rows={3}
        />

        <div className="composer-footer">
          <div className="composer-tools">
            <button
              type="button"
              className="composer-add-button"
              title={uploading ? "正在上传文件" : "上传文件"}
              aria-label="上传文件"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || streaming || uploading}
            >
              +
            </button>
            {files.slice(0, 4).map((file) => (
              <button
                type="button"
                key={file.id}
                className="composer-file-chip"
                title={file.fileName}
                onClick={() => onPreviewFile?.(file.id)}
              >
                {file.fileName}
              </button>
            ))}
            {files.length > 4 && (
              <span className="composer-file-count">+{files.length - 4}</span>
            )}
            <span className="input-model-hint">
              模型：{selectedModelName || "后端默认"}
            </span>
          </div>

          <div className="input-actions">
            {streaming ? (
              <button type="button" className="stop-button" onClick={onStop}>
                停止
              </button>
            ) : (
              <button
                type="button"
                className="send-button"
                onClick={submit}
                disabled={disabled || !value.trim()}
              >
                发送
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
