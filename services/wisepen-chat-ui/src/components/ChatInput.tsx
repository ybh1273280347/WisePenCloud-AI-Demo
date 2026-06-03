import {ChangeEvent, DragEvent, KeyboardEvent, useRef, useState} from "react";
import {parseDraggedResource} from "../mock/noteMock";
import type {ChatFileItem, ChatResourceRef} from "../types/chat";

type ChatInputProps = {
  disabled: boolean;
  streaming: boolean;
  selectedModelName?: string;
  files?: ChatFileItem[];
  resourceRefs?: ChatResourceRef[];
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onUploadFile?: (file: File) => Promise<void>;
  onPreviewFile?: (fileId: string) => void;
  onAttachResource?: (resource: ChatResourceRef) => void;
  onRemoveResource?: (resourceId: string) => void;
};

export function ChatInput({
  disabled,
  streaming,
  selectedModelName,
  files = [],
  resourceRefs = [],
  value,
  onValueChange,
  onSend,
  onStop,
  onUploadFile,
  onPreviewFile,
  onAttachResource,
  onRemoveResource,
}: ChatInputProps) {
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const uploadedFiles = files.filter((file) => file.source === "upload");

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

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!parseDraggedResource(event.dataTransfer)) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDragOver(true);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    const resource = parseDraggedResource(event.dataTransfer);
    if (!resource || !onAttachResource) {
      setDragOver(false);
      return;
    }
    event.preventDefault();
    onAttachResource({
      resourceId: resource.resourceId,
      resourceType: resource.resourceType,
      title: resource.title,
      source: "drag",
    });
    setDragOver(false);
  }

  return (
    <div className="chat-input-wrap">
      {uploadedFiles.length > 0 ? (
        <div className="composer-attachment-tray" aria-label="已选择附件">
          {uploadedFiles.map((file) => (
            <button
              type="button"
              key={file.id}
              className="composer-attachment-card"
              title={file.fileRef ? `${file.fileName}\n${file.fileRef}` : file.fileName}
              onClick={() => onPreviewFile?.(file.id)}
            >
              <span className="composer-attachment-icon" aria-hidden="true">
                □
              </span>
              <span className="composer-attachment-body">
                <strong>{file.fileName}</strong>
                <small>{formatAttachmentMeta(file)}</small>
              </span>
            </button>
          ))}
        </div>
      ) : null}
      <div
        className={`chat-input-bar ${dragOver ? "chat-input-bar-drop" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
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
            {resourceRefs.map((resource) => (
              <button
                type="button"
                key={resource.resourceId}
                className="composer-resource-chip"
                title={`${resource.resourceType}: ${resource.resourceId}`}
                onClick={() => onRemoveResource?.(resource.resourceId)}
              >
                {resource.resourceType}: {resource.title} ×
              </button>
            ))}
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

function formatAttachmentMeta(file: ChatFileItem): string {
  const size = file.sizeBytes ? formatBytes(file.sizeBytes) : "";
  const type = file.contentType || "文件";
  return size ? `${type} · ${size}` : type;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
