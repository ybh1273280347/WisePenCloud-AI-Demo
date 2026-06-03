export type MockNoteBlock = {
  id: string;
  type: "heading" | "paragraph" | "quote" | "chart-placeholder";
  level?: number;
  text: string;
  children?: MockNoteBlock[];
};

export type MockNote = {
  resourceId: string;
  title: string;
  authors: string[];
  lastEditedAtText: string;
  blocks: MockNoteBlock[];
};

export type MockResourceRef = {
  type: "resource" | "link";
  resourceId: string;
  title: string;
  resourceType: "document" | "note" | "skill" | string;
};

export const DRIVE_NODE_DND_TYPE = "application/x-wisepen-drivenode";

const blocks: MockNoteBlock[] = [
  {
    id: "block_metrics",
    type: "chart-placeholder",
    text: "mock note table provider 对应的 block：traceable_chart_from_note 会把 source_map 指向这里。",
  },
  {
    id: "block_exec_summary",
    type: "heading",
    level: 1,
    text: "可信图表与 md2ppt 测试笔记",
  },
  {
    id: "block_source_claim",
    type: "paragraph",
    text: "这段文字模拟原始 Note 中可被 PPT 页面、图表标题、caption 或 annotation 引用的来源内容。",
  },
  {
    id: "block_chart_requirement",
    type: "heading",
    level: 2,
    text: "图表生成要求",
  },
  {
    id: "block_chart_requirement_body",
    type: "paragraph",
    text: "每张图必须记录 source_data_ref、data_fingerprint、spec_fingerprint、render_fingerprint 与 ChartManifest。",
  },
  {
    id: "block_chart_anchor",
    type: "chart-placeholder",
    text: "模拟图表锚点：后续 SVG/PPT 中的可点击元素可以跳回这个 block。",
  },
  {
    id: "block_ppt_roundtrip",
    type: "heading",
    level: 2,
    text: "PPT 互跳链路",
  },
  {
    id: "block_ppt_roundtrip_body",
    type: "paragraph",
    text: "PPT 内部元素点击跳回 Note 时，应携带 note_resource_id、block_id、ppt_resource_id、slide_id 与 element_id。",
  },
  {
    id: "block_quote",
    type: "quote",
    text: "这是一段用于测试 derivation_kind=source_derived 的引用文本。",
  },
];

export const MOCK_NOTES: Record<string, MockNote> = {
  "mock-note-1": {
    resourceId: "mock-note-1",
    title: "可信追溯 md2ppt 内测笔记",
    authors: ["内测开发者"],
    lastEditedAtText: "2026-06-02 10:00",
    blocks,
  },
  note_mock: {
    resourceId: "note_mock",
    title: "Mock Note Table Provider 对齐笔记",
    authors: ["内测开发者"],
    lastEditedAtText: "2026-06-02 12:00",
    blocks,
  },
};

export const MOCK_RESOURCES: MockResourceRef[] = [
  {
    type: "resource",
    resourceId: "mock-note-1",
    title: "可信追溯 md2ppt 内测笔记",
    resourceType: "note",
  },
  {
    type: "resource",
    resourceId: "note_mock",
    title: "Mock Note Table Provider 对齐笔记",
    resourceType: "note",
  },
  {
    type: "resource",
    resourceId: "mock-doc-1",
    title: "Chart Compiler 需求文档.pdf",
    resourceType: "document",
  },
];

export function getMockNote(resourceId: string): MockNote {
  const saved = loadLocalMockNote(resourceId);
  if (saved) {
    return saved;
  }

  return (
    MOCK_NOTES[resourceId] ?? {
      resourceId,
      title: `Mock Note ${resourceId}`,
      authors: ["内测开发者"],
      lastEditedAtText: "本地 mock",
      blocks,
    }
  );
}

export function saveLocalMockNote(note: MockNote): void {
  window.localStorage.setItem(localNoteKey(note.resourceId), JSON.stringify(note));
}

export function resetLocalMockNote(resourceId: string): MockNote {
  window.localStorage.removeItem(localNoteKey(resourceId));
  return getMockNote(resourceId);
}

function loadLocalMockNote(resourceId: string): MockNote | null {
  const raw = window.localStorage.getItem(localNoteKey(resourceId));
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<MockNote>;
    if (
      parsed.resourceId === resourceId &&
      typeof parsed.title === "string" &&
      Array.isArray(parsed.blocks)
    ) {
      return {
        resourceId,
        title: parsed.title,
        authors: Array.isArray(parsed.authors) ? parsed.authors : ["内测开发者"],
        lastEditedAtText: typeof parsed.lastEditedAtText === "string" ? parsed.lastEditedAtText : "本地 mock",
        blocks: parsed.blocks as MockNoteBlock[],
      };
    }
  } catch {
    return null;
  }
  return null;
}

function localNoteKey(resourceId: string): string {
  return `wisepen-chat-ui:mock-note:${resourceId}`;
}

export function parseDraggedResource(dataTransfer: DataTransfer): MockResourceRef | null {
  const raw = dataTransfer.getData(DRIVE_NODE_DND_TYPE);
  if (!raw) {
    return null;
  }

  try {
    const node = JSON.parse(raw) as Partial<MockResourceRef>;
    if (
      (node.type === "resource" || node.type === "link") &&
      typeof node.resourceId === "string" &&
      typeof node.title === "string" &&
      typeof node.resourceType === "string"
    ) {
      return {
        type: node.type,
        resourceId: node.resourceId,
        title: node.title,
        resourceType: node.resourceType,
      };
    }
  } catch {
    return null;
  }

  return null;
}
