import {useEffect, useMemo, useRef, useState} from "react";
import {
  DRIVE_NODE_DND_TYPE,
  getMockNote,
  MOCK_RESOURCES,
  resetLocalMockNote,
  saveLocalMockNote,
  type MockNote,
  type MockNoteBlock,
} from "../mock/noteMock";

type MockNotePageProps = {
  resourceId: string;
  blockId?: string | null;
  onOpenChat: () => void;
};

function flattenBlocks(blocks: MockNoteBlock[]): MockNoteBlock[] {
  const flat: MockNoteBlock[] = [];
  for (const block of blocks) {
    flat.push(block);
    if (block.children?.length) {
      flat.push(...flattenBlocks(block.children));
    }
  }
  return flat;
}

function blockClassName(block: MockNoteBlock, activeBlockId?: string | null): string {
  const classes = ["mock-note-block", `mock-note-block-${block.type}`];
  if (block.id === activeBlockId) {
    classes.push("mock-note-block-active");
  }
  return classes.join(" ");
}

export function MockNotePage({resourceId, blockId, onOpenChat}: MockNotePageProps) {
  const [note, setNote] = useState<MockNote>(() => getMockNote(resourceId));
  const [activeBlockId, setActiveBlockId] = useState<string | null>(blockId ?? null);
  const [saveState, setSaveState] = useState<"saved" | "dirty">("saved");
  const blockRefs = useRef<Record<string, HTMLElement | null>>({});
  const flatBlocks = useMemo(() => flattenBlocks(note.blocks), [note.blocks]);
  const outline = flatBlocks.filter((block) => block.type === "heading");

  useEffect(() => {
    setNote(getMockNote(resourceId));
    setSaveState("saved");
  }, [resourceId]);

  function navigateToBlock(nextBlockId: string, updateUrl = true) {
    setActiveBlockId(nextBlockId);
    const target = blockRefs.current[nextBlockId];
    target?.scrollIntoView({block: "center", behavior: "smooth"});
    target?.focus({preventScroll: true});

    if (updateUrl) {
      const next = `/app/note/${encodeURIComponent(resourceId)}?blockId=${encodeURIComponent(nextBlockId)}`;
      window.history.pushState({}, "", next);
    }
  }

  useEffect(() => {
    if (!blockId) {
      return;
    }
    window.requestAnimationFrame(() => navigateToBlock(blockId, false));
  }, [blockId, resourceId]);

  function updateBlockText(blockIdToUpdate: string, text: string) {
    setNote((current) => ({
      ...current,
      lastEditedAtText: "本地未保存",
      blocks: updateBlocks(current.blocks, blockIdToUpdate, text),
    }));
    setSaveState("dirty");
  }

  function saveNote() {
    const nextNote = {
      ...note,
      lastEditedAtText: new Date().toLocaleString(),
    };
    saveLocalMockNote(nextNote);
    setNote(nextNote);
    setSaveState("saved");
  }

  function resetNote() {
    const nextNote = resetLocalMockNote(resourceId);
    setNote(nextNote);
    setSaveState("saved");
  }

  function renderBlock(block: MockNoteBlock) {
    const commonProps = {
      key: block.id,
      id: block.id,
      tabIndex: -1,
      ref: (node: HTMLElement | null) => {
        blockRefs.current[block.id] = node;
      },
      className: blockClassName(block, activeBlockId),
      "data-block-id": block.id,
      "data-content-type": block.type,
    };

    if (block.type === "heading") {
      const Heading = block.level === 1 ? "h1" : "h2";
      return (
        <Heading
          {...commonProps}
          contentEditable
          suppressContentEditableWarning
          onBlur={(event) => updateBlockText(block.id, event.currentTarget.textContent || "")}
        >
          {block.text}
        </Heading>
      );
    }
    if (block.type === "quote") {
      return (
        <blockquote
          {...commonProps}
          contentEditable
          suppressContentEditableWarning
          onBlur={(event) => updateBlockText(block.id, event.currentTarget.textContent || "")}
        >
          {block.text}
        </blockquote>
      );
    }
    if (block.type === "chart-placeholder") {
      return (
        <section {...commonProps}>
          <div className="mock-chart-box">
            <div className="mock-chart-bars" aria-hidden>
              <span style={{height: "48%"}} />
              <span style={{height: "76%"}} />
              <span style={{height: "34%"}} />
              <span style={{height: "62%"}} />
            </div>
            <div>
              <strong>Chart provenance anchor</strong>
              <p
                contentEditable
                suppressContentEditableWarning
                onBlur={(event) => updateBlockText(block.id, event.currentTarget.textContent || "")}
              >
                {block.text}
              </p>
              <a href={`/app/note/${encodeURIComponent(resourceId)}?blockId=${encodeURIComponent(block.id)}`}>
                测试跳回本块
              </a>
            </div>
          </div>
        </section>
      );
    }
    return (
      <p
        {...commonProps}
        contentEditable
        suppressContentEditableWarning
        onBlur={(event) => updateBlockText(block.id, event.currentTarget.textContent || "")}
      >
        {block.text}
      </p>
    );
  }

  return (
    <div className="mock-note-shell">
      <aside className="mock-resource-dock" aria-label="可拖拽资源">
        <div className="mock-resource-dock-title">Mock resources</div>
        {MOCK_RESOURCES.map((resource) => (
          <div
            key={resource.resourceId}
            className="mock-resource-chip"
            draggable
            onDragStart={(event) => {
              event.dataTransfer.setData(DRIVE_NODE_DND_TYPE, JSON.stringify(resource));
              event.dataTransfer.effectAllowed = "copy";
            }}
          >
            <span>{resource.resourceType}</span>
            <strong>{resource.title}</strong>
            <small>{resource.resourceId}</small>
          </div>
        ))}
      </aside>

      <main className="mock-note-main">
        <header className="mock-note-header">
          <button type="button" className="mock-note-back" onClick={onOpenChat}>
            返回对话
          </button>
          <div>
            <div className="mock-note-kicker">/app/note/{note.resourceId}</div>
            <input
              className="mock-note-title-input"
              value={note.title}
              onChange={(event) => {
                setNote((current) => ({...current, title: event.target.value, lastEditedAtText: "本地未保存"}));
                setSaveState("dirty");
              }}
            />
            <p>
              {note.authors.join(", ")} · {note.lastEditedAtText}
            </p>
          </div>
          <div className="mock-note-actions">
            <span className={`mock-note-save-state mock-note-save-state-${saveState}`}>
              {saveState === "dirty" ? "未保存" : "已保存到本地"}
            </span>
            <button type="button" onClick={saveNote}>保存</button>
            <button type="button" onClick={resetNote}>重置</button>
          </div>
        </header>

        <div className="mock-note-layout">
          <nav className="mock-note-outline" aria-label="文档目录">
            <div className="mock-note-outline-title">目录</div>
            {outline.map((item) => (
              <button
                key={item.id}
                type="button"
                className={item.id === activeBlockId ? "active" : ""}
                onClick={() => navigateToBlock(item.id)}
              >
                {item.text}
              </button>
            ))}
          </nav>

          <article className="mock-note-document">{flatBlocks.map(renderBlock)}</article>
        </div>
      </main>
    </div>
  );
}

function updateBlocks(blocks: MockNoteBlock[], blockId: string, text: string): MockNoteBlock[] {
  return blocks.map((block) => {
    const nextBlock = block.id === blockId ? {...block, text} : block;
    if (!nextBlock.children?.length) {
      return nextBlock;
    }
    return {
      ...nextBlock,
      children: updateBlocks(nextBlock.children, blockId, text),
    };
  });
}
