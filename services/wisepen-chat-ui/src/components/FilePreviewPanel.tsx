import { useEffect, useMemo, useState } from "react";
import { fetchFileBlob } from "../api/file";
import type { ChatFileItem } from "../types/chat";

type PreviewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; objectUrl: string; text?: string; contentType: string }
  | { status: "error"; message: string };

type FilePreviewPanelProps = {
  files: ChatFileItem[];
  selectedFileId: string;
  onSelectFile: (fileId: string) => void;
  onDelete: (file: ChatFileItem) => Promise<void>;
  onClose: () => void;
};

export function FilePreviewPanel({
  files,
  selectedFileId,
  onSelectFile,
  onDelete,
  onClose,
}: FilePreviewPanelProps) {
  const [preview, setPreview] = useState<PreviewState>({ status: "idle" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const selectedFile = useMemo(
    () => files.find((file) => file.id === selectedFileId) || null,
    [files, selectedFileId],
  );
  const uploadedFiles = files.filter((file) => file.source === "upload");
  const generatedFiles = files.filter((file) => file.source === "generated");

  useEffect(() => {
    if (!selectedFile) {
      setPreview({ status: "idle" });
      return;
    }

    const file = selectedFile;
    let cancelled = false;
    let objectUrl: string | null = null;
    setPreview({ status: "loading" });

    async function loadPreview() {
      try {
        const blob = await fetchFileBlob(file.previewUrl);
        const contentType = blob.type || file.contentType || "application/octet-stream";
        objectUrl = URL.createObjectURL(blob);
        const text = shouldReadAsText(contentType, file.fileName, blob.size)
          ? await blob.text()
          : undefined;

        if (cancelled) {
          if (objectUrl) {
            URL.revokeObjectURL(objectUrl);
          }
          return;
        }

        setPreview({ status: "ready", objectUrl, text, contentType });
      } catch (error) {
        if (!cancelled) {
          setPreview({
            status: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      }
    }

    loadPreview();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [selectedFile]);

  async function handleDownload(file: ChatFileItem) {
    setDownloadingId(file.id);
    setActionError(null);
    try {
      const blob = await fetchFileBlob(file.downloadUrl);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = file.fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadingId(null);
    }
  }

  async function handleDelete(file: ChatFileItem) {
    setActionError(null);
    try {
      await onDelete(file);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <aside className="file-panel file-panel-drawer">
      <div className="file-panel-header">
        <div>
          <div className="sidebar-label">文件</div>
          <h2>文件预览</h2>
        </div>
        <button
          type="button"
          className="file-panel-close"
          onClick={onClose}
          aria-label="关闭预览"
        >
          ×
        </button>
      </div>

      <p className="file-panel-note">
        点击文件时才打开预览；上传文件会随下一条消息以 file_ref 附加给后端。
      </p>

      {actionError ? <div className="file-panel-error">{actionError}</div> : null}

      <FileGroup
        title="自己的文件"
        emptyText="还没有上传文件。"
        files={uploadedFiles}
        selectedFileId={selectedFile?.id || null}
        onSelectFile={onSelectFile}
        onDelete={handleDelete}
        onDownload={handleDownload}
        downloadingId={downloadingId}
      />

      <FileGroup
        title="模型输出"
        emptyText="模型生成文件后会显示在这里。"
        files={generatedFiles}
        selectedFileId={selectedFile?.id || null}
        onSelectFile={onSelectFile}
        onDelete={handleDelete}
        onDownload={handleDownload}
        downloadingId={downloadingId}
      />

      <div className="preview-card">
        <div className="preview-card-header">
          <div>
            <div className="sidebar-label">预览</div>
            <strong>{selectedFile?.fileName || "未选择文件"}</strong>
          </div>
          {selectedFile ? (
            <button
              type="button"
              className="preview-download-button"
              disabled={downloadingId === selectedFile.id}
              onClick={() => handleDownload(selectedFile)}
            >
              下载
            </button>
          ) : null}
        </div>
        {selectedFile ? renderPreview(selectedFile, preview) : <p className="preview-empty">选择文件查看预览。</p>}
      </div>
    </aside>
  );
}

type FileGroupProps = {
  title: string;
  emptyText: string;
  files: ChatFileItem[];
  selectedFileId: string | null;
  downloadingId: string | null;
  onSelectFile: (fileId: string) => void;
  onDelete: (file: ChatFileItem) => Promise<void>;
  onDownload: (file: ChatFileItem) => Promise<void>;
};

function FileGroup({
  title,
  emptyText,
  files,
  selectedFileId,
  downloadingId,
  onSelectFile,
  onDelete,
  onDownload,
}: FileGroupProps) {
  return (
    <section className="file-group">
      <div className="file-group-title">{title}</div>
      {files.length === 0 ? (
        <p className="file-empty">{emptyText}</p>
      ) : (
        <div className="file-list">
          {files.map((file) => (
            <button
              type="button"
              key={file.id}
              className={`file-row ${selectedFileId === file.id ? "file-row-active" : ""}`}
              onClick={() => onSelectFile(file.id)}
            >
              <span className="file-row-main">
                <strong>{file.fileName}</strong>
                <small>{formatFileMeta(file)}</small>
              </span>
              <span className="file-row-actions">
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation();
                    onDownload(file);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      event.stopPropagation();
                      onDownload(file);
                    }
                  }}
                >
                  {downloadingId === file.id ? "..." : "下载"}
                </span>
                {file.source === "upload" ? (
                  <span
                    role="button"
                    tabIndex={0}
                    className="file-delete-action"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(file);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        event.stopPropagation();
                        onDelete(file);
                      }
                    }}
                  >
                    删除
                  </span>
                ) : null}
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function renderPreview(file: ChatFileItem, preview: PreviewState) {
  if (preview.status === "loading") {
    return <p className="preview-empty">正在加载预览...</p>;
  }
  if (preview.status === "error") {
    return <p className="preview-error">{preview.message}</p>;
  }
  if (preview.status !== "ready") {
    return <p className="preview-empty">选择文件查看预览。</p>;
  }

  if (preview.text != null) {
    return <pre className="preview-text">{preview.text}</pre>;
  }

  if (preview.contentType.startsWith("image/")) {
    return <img className="preview-image" src={preview.objectUrl} alt={file.fileName} />;
  }

  if (preview.contentType === "application/pdf" || file.fileName.toLowerCase().endsWith(".pdf")) {
    return <iframe className="preview-frame" src={preview.objectUrl} title={file.fileName} />;
  }

  if (preview.contentType === "text/html" || file.fileName.toLowerCase().endsWith(".html")) {
    return <iframe className="preview-frame" src={preview.objectUrl} title={file.fileName} sandbox="" />;
  }

  return (
    <p className="preview-empty">
      这个格式暂不支持内嵌预览，请点击下载查看。
    </p>
  );
}

function shouldReadAsText(contentType: string, fileName: string, size: number): boolean {
  if (size > 2 * 1024 * 1024) {
    return false;
  }
  const lowerName = fileName.toLowerCase();
  return (
    contentType.startsWith("text/") ||
    contentType.includes("json") ||
    contentType.includes("xml") ||
    [".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"].some((suffix) =>
      lowerName.endsWith(suffix),
    )
  );
}

function formatFileMeta(file: ChatFileItem): string {
  const size = file.sizeBytes == null ? "" : ` · ${formatBytes(file.sizeBytes)}`;
  const source = file.source === "upload" ? "已上传" : "已生成";
  return `${source}${size}`;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}
