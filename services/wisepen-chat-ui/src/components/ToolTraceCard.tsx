import {useState} from "react";
import {extractGeneratedFilesFromToolOutput, fetchFileBlob} from "../api/file";
import type {AssistantPart, ChatFileItem} from "../types/chat";
import {formatDuration} from "../utils/formatDuration";
import {Badge} from "./Badge";
import {JsonBlock} from "./JsonBlock";
import {ToolMascotPopup} from "./ToolMascotPopup";

type ToolCallPart = Extract<AssistantPart, { type: "tool_call" }>;

type ToolTraceCardProps = {
  part: ToolCallPart;
};

export function ToolTraceCard({ part }: ToolTraceCardProps) {
  const isError = part.status === "error";
  const hasOutput = Boolean(part.output);
  const outputSummary = summarizeValue(part.output);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const generatedFiles =
    typeof part.output === "string" && !isError
      ? extractGeneratedFilesFromToolOutput(part.output)
      : [];

  async function downloadGeneratedFile(file: ChatFileItem) {
    setDownloadingId(file.id);
    setDownloadError(null);
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
      setDownloadError(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <section className={`tool-trace-card tool-trace-card-${part.status}`}>
      <ToolMascotPopup status={part.status} />

      <div className="tool-card-header">
        <div className="tool-card-title-group">
          <div className="tool-title">
            {part.status === "running" ? <span className="running-dot" /> : null}
            <span>{part.toolName || "工具"}</span>
          </div>
          <div className="tool-call-id">call_id: {part.callId}</div>
        </div>

        <div className="tool-meta">
          <Badge status={part.status} />
          <span>{formatDuration(part.startedAt, part.completedAt)}</span>
        </div>
      </div>

      <div className="tool-summary-grid">
        <div>
          <span className="tool-field-label">输入</span>
          <p>{part.input == null ? "等待参数" : summarizeValue(part.input)}</p>
        </div>
        <div>
          <span className="tool-field-label">{isError ? "错误" : "结果"}</span>
          <p>{hasOutput ? outputSummary : "等待输出"}</p>
        </div>
      </div>

      {generatedFiles.length > 0 ? (
        <div className="generated-document-list">
          {generatedFiles.map((file) => (
            <GeneratedDocumentCard
              key={file.id}
              file={file}
              downloading={downloadingId === file.id}
              onDownload={downloadGeneratedFile}
            />
          ))}
          {downloadError ? (
            <div className="generated-document-error">{downloadError}</div>
          ) : null}
        </div>
      ) : null}

      <JsonBlock value={part.input} label="原始输入 JSON" defaultOpen={false} />
      <JsonBlock value={part.output} label="原始输出" defaultOpen={false} error={isError} />
    </section>
  );
}

function GeneratedDocumentCard({
  file,
  downloading,
  onDownload,
}: {
  file: ChatFileItem;
  downloading: boolean;
  onDownload: (file: ChatFileItem) => Promise<void>;
}) {
  return (
    <div className="generated-document-card">
      <div className="generated-document-main">
        <strong>{file.fileName}</strong>
        <small>{formatGeneratedDocumentMeta(file)}</small>
      </div>
      <a
        href={file.downloadUrl}
        download={file.fileName}
        className="generated-document-download"
        aria-disabled={downloading}
        onClick={(event) => {
          event.preventDefault();
          if (!downloading) {
            void onDownload(file);
          }
        }}
      >
        {downloading ? "下载中..." : "下载文件"}
      </a>
    </div>
  );
}

function summarizeValue(value: unknown): string {
  if (value == null || value === "") {
    return "空";
  }

  const raw = typeof value === "string" ? value : stringifyForSummary(value);
  const compact = raw.replace(/\s+/g, " ").trim();
  if (compact.length <= 360) {
    return compact;
  }
  return `${compact.slice(0, 360)}...`;
}

function stringifyForSummary(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function formatGeneratedDocumentMeta(file: ChatFileItem): string {
  const size = file.sizeBytes == null ? "" : ` · ${formatBytes(file.sizeBytes)}`;
  return `${file.contentType || "application/octet-stream"}${size}`;
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
