// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { extractGeneratedFilesFromToolOutput } from "../api/file";
import { ToolTraceCard } from "./ToolTraceCard";

const generatedDocumentOutput = [
  "[Generated Document]",
  "- download_ref: session/复旦大学计算机_信息概要.md",
  "- download_url: /api/document-export/download?ref=session%2F%E5%A4%8D%E6%97%A6%E5%A4%A7%E5%AD%A6%E8%AE%A1%E7%AE%97%E6%9C%BA_%E4%BF%A1%E6%81%AF%E6%A6%82%E8%A6%81.md",
  "- file_name: 复旦大学计算机_信息概要.md",
  "- target_format: markdown",
  "- content_type: text/markdown; charset=utf-8",
  "- size_bytes: 2107",
].join("\n");

describe("generated document downloads", () => {
  it("extracts download_url from generated document tool output", () => {
    const [file] = extractGeneratedFilesFromToolOutput(generatedDocumentOutput);

    expect(file.fileName).toBe("复旦大学计算机_信息概要.md");
    expect(file.downloadRef).toBe("session/复旦大学计算机_信息概要.md");
    expect(file.downloadUrl).toBe(
      "/api/document-export/download?ref=session%2F%E5%A4%8D%E6%97%A6%E5%A4%A7%E5%AD%A6%E8%AE%A1%E7%AE%97%E6%9C%BA_%E4%BF%A1%E6%81%AF%E6%A6%82%E8%A6%81.md",
    );
    expect(file.previewUrl).toBe(file.downloadUrl);
  });

  it("does not create a generated file link without download_url", () => {
    const outputWithoutUrl = [
      "[Generated Document]",
      "- download_ref: session/report.md",
      "- file_name: report.md",
    ].join("\n");

    expect(extractGeneratedFilesFromToolOutput(outputWithoutUrl)).toEqual([]);
  });

  it("renders a real download link for generated documents", () => {
    render(
      <ToolTraceCard
        part={{
          type: "tool_call",
          id: "tool_1",
          callId: "call_1",
          toolName: "document_export",
          status: "completed",
          output: generatedDocumentOutput,
          startedAt: 0,
          completedAt: 1,
        }}
      />,
    );

    const link = screen.getByRole("link", { name: "下载文件" }) as HTMLAnchorElement;

    expect(screen.getByText("复旦大学计算机_信息概要.md")).toBeTruthy();
    expect(link.getAttribute("href")).toBe(
      "/api/document-export/download?ref=session%2F%E5%A4%8D%E6%97%A6%E5%A4%A7%E5%AD%A6%E8%AE%A1%E7%AE%97%E6%9C%BA_%E4%BF%A1%E6%81%AF%E6%A6%82%E8%A6%81.md",
    );
    expect(link.getAttribute("download")).toBe("复旦大学计算机_信息概要.md");
  });
});
