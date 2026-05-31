import type {ChatFileItem} from "../types/chat";
import {apiFetch, ensureApiOk, readApiData} from "./client";

type UploadedChatFileDto = {
  file_id: string;
  file_ref: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  preview_url: string;
  download_url: string;
};

type GeneratedFileFields = {
  downloadRef?: string;
  downloadUrl: string;
  fileName?: string;
  targetFormat?: string;
  contentType?: string;
  sizeBytes?: number;
};

export async function uploadChatFile(sessionId: string, file: File): Promise<ChatFileItem> {
  const params = new URLSearchParams({
    session_id: sessionId,
    file_name: file.name,
  });

  const response = await apiFetch(`/chat/file/upload?${params.toString()}`, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
    },
    body: file,
  });
  const data = await readApiData<UploadedChatFileDto>(response, "Failed to upload file");
  return {
    id: `upload:${data.file_id}`,
    source: "upload",
    fileId: data.file_id,
    fileRef: data.file_ref,
    fileName: data.file_name,
    contentType: data.content_type,
    sizeBytes: data.size_bytes,
    previewUrl: data.preview_url,
    downloadUrl: data.download_url,
    createdAt: Date.now(),
  };
}

export async function listChatFiles(sessionId: string): Promise<ChatFileItem[]> {
  const params = new URLSearchParams({ session_id: sessionId });
  const response = await apiFetch(`/chat/file/list?${params.toString()}`);
  const data = await readApiData<UploadedChatFileDto[]>(response, "Failed to list files");
  return data.map((file) => ({
    id: `upload:${file.file_id}`,
    source: "upload",
    fileId: file.file_id,
    fileRef: file.file_ref,
    fileName: file.file_name,
    contentType: file.content_type,
    sizeBytes: file.size_bytes,
    previewUrl: file.preview_url,
    downloadUrl: file.download_url,
    createdAt: Date.now(),
  }));
}

export async function deleteChatFile(sessionId: string, file: ChatFileItem): Promise<void> {
  if (file.source !== "upload" || !file.fileId) {
    return;
  }

  const params = new URLSearchParams({
    session_id: sessionId,
    file_id: file.fileId,
  });
  const response = await apiFetch(`/chat/file/delete?${params.toString()}`, {
    method: "DELETE",
  });
  await ensureApiOk(response, "Failed to delete file");
}

export async function fetchFileBlob(urlPath: string): Promise<Blob> {
  const response = await apiFetch(urlPath, {
    method: "GET",
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Failed to load file preview. HTTP ${response.status}: ${body.slice(0, 240)}`);
  }
  return response.blob();
}

export function extractGeneratedFilesFromToolOutput(output: string): ChatFileItem[] {
  if (!output.includes("download_ref") && !output.includes("download_url")) {
    return [];
  }

  const results: ChatFileItem[] = [];
  const blocks = output.split(/\n(?=\[Generated Document\])/g);
  for (const block of blocks) {
    const downloadRef = readField(block, "download_ref");
    const downloadUrl = readField(block, "download_url");
    if (!downloadUrl) {
      continue;
    }

    results.push(
      createGeneratedFile({
        downloadRef,
        downloadUrl,
        fileName: readField(block, "file_name") || downloadRef?.split("/").pop() || "generated-file",
        targetFormat: readField(block, "target_format"),
        contentType: readField(block, "content_type") || "application/octet-stream",
        sizeBytes: parseOptionalNumber(readField(block, "size_bytes")),
      }),
    );
  }

  const seenIds = new Set(results.map((file) => file.id));
  for (const parsed of extractGeneratedDocumentJson(output)) {
    const file = createGeneratedFile(parsed);
    if (seenIds.has(file.id)) {
      continue;
    }
    seenIds.add(file.id);
    results.push(file);
  }

  return results;
}

export function createGeneratedFile(fields: GeneratedFileFields): ChatFileItem {
  const stableRef = fields.downloadRef || fields.downloadUrl || fields.fileName || "generated-file";
  return {
    id: `generated:${stableRef}`,
    source: "generated",
    downloadRef: fields.downloadRef,
    fileName: fields.fileName || fields.downloadRef?.split("/").pop() || "generated-file",
    contentType: fields.contentType || "application/octet-stream",
    sizeBytes: fields.sizeBytes,
    previewUrl: fields.downloadUrl,
    downloadUrl: fields.downloadUrl,
    createdAt: Date.now(),
  };
}

function extractGeneratedDocumentJson(output: string): GeneratedFileFields[] {
  const matches = output.match(/\{[\s\S]*?\}/g) || [];
  return matches
    .map((chunk) => {
      try {
        return JSON.parse(chunk) as Record<string, unknown>;
      } catch {
        return null;
      }
    })
    .filter((value): value is Record<string, unknown> => Boolean(value))
    .map((value) => ({
      downloadRef: stringField(value.download_ref),
      downloadUrl: stringField(value.download_url) || "",
      fileName: stringField(value.file_name),
      targetFormat: stringField(value.target_format),
      contentType: stringField(value.content_type),
      sizeBytes: numberField(value.size_bytes),
    }))
    .filter((value) => value.downloadUrl);
}

function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function numberField(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function readField(text: string, fieldName: string): string | undefined {
  const pattern = new RegExp(`^\\s*-\\s*${fieldName}:\\s*(.+?)\\s*$`, "im");
  const match = text.match(pattern);
  return match?.[1]?.trim();
}

function parseOptionalNumber(value: string | undefined): number | undefined {
  if (!value) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}
