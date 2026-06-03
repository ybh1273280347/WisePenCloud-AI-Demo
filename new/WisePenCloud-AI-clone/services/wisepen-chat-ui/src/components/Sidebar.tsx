import {FormEvent, MouseEvent, useEffect, useMemo, useRef, useState} from "react";
import {createPortal} from "react-dom";
import type {ChatSession} from "../types/chat";

type SidebarProps = {
  currentId: string | null;
  items: ChatSession[];
  streaming: boolean;
  loadingHistory: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onPin: (id: string, setPin: boolean) => Promise<void>;
};

export function Sidebar({
  currentId,
  items,
  streaming,
  loadingHistory,
  onNewChat,
  onSelect,
  onRename,
  onDelete,
  onPin,
}: SidebarProps) {
  const sidebarRef = useRef<HTMLElement | null>(null);
  const [menuPlacement, setMenuPlacement] = useState<{ id: string; left: number; top: number } | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [pendingAction, setPendingAction] = useState<"rename" | "delete" | "pin" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const menuItem = useMemo(
    () => items.find((item) => item.id === menuPlacement?.id) || null,
    [items, menuPlacement?.id],
  );

  useEffect(() => {
    if (streaming) {
      setMenuPlacement(null);
    }
  }, [streaming]);

  useEffect(() => {
    if (!menuPlacement) {
      return;
    }
    const closeMenu = () => setMenuPlacement(null);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    return () => {
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, [menuPlacement]);

  function startRename(item: ChatSession) {
    setMenuPlacement(null);
    setActionError(null);
    setEditingId(item.id);
    setDraftTitle(item.title);
  }

  function toggleMenu(event: MouseEvent<HTMLButtonElement>, item: ChatSession) {
    const rowRect = event.currentTarget.closest(".session-row")?.getBoundingClientRect();
    const sidebarRect = sidebarRef.current?.getBoundingClientRect();
    setMenuPlacement((current) => {
      if (current?.id === item.id) {
        return null;
      }
      return {
        id: item.id,
        left: Math.max(0, Math.min(sidebarRect?.right ?? 0, window.innerWidth - 128)),
        top: Math.max(10, Math.min(rowRect?.top ?? 10, window.innerHeight - 150)),
      };
    });
  }

  async function submitRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingId || !draftTitle.trim()) {
      return;
    }
    setPendingAction("rename");
    setActionError(null);
    try {
      await onRename(editingId, draftTitle.trim());
      setEditingId(null);
      setDraftTitle("");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function runDelete(item: ChatSession) {
    setPendingAction("delete");
    setActionError(null);
    try {
      await onDelete(item.id);
      setMenuPlacement(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function runPin(item: ChatSession) {
    setPendingAction("pin");
    setActionError(null);
    try {
      await onPin(item.id, !item.isPinned);
      setMenuPlacement(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setPendingAction(null);
    }
  }

  const floatingMenu =
    menuItem && menuPlacement ? (
      <div
        className="session-menu session-menu-floating"
        style={{ left: menuPlacement.left, top: menuPlacement.top }}
        role="menu"
      >
        <button
          type="button"
          role="menuitem"
          disabled={pendingAction !== null}
          onClick={() => startRename(menuItem)}
        >
          重命名
        </button>
        <button
          type="button"
          role="menuitem"
          disabled={pendingAction !== null}
          onClick={() => runDelete(menuItem)}
        >
          {pendingAction === "delete" ? "删除中..." : "删除"}
        </button>
        <button
          type="button"
          role="menuitem"
          disabled={pendingAction !== null}
          onClick={() => runPin(menuItem)}
        >
          {pendingAction === "pin" ? "处理中..." : menuItem.isPinned ? "取消置顶" : "置顶"}
        </button>
        {actionError ? <div className="session-menu-error">{actionError}</div> : null}
      </div>
    ) : null;

  return (
    <>
      <aside className="sidebar" ref={sidebarRef}>
      <div className="sidebar-header">
        <div className="brand">WisePen 对话</div>
        <button type="button" className="new-chat-button" onClick={onNewChat} disabled={streaming}>
          新建对话
        </button>
      </div>

      <div className="sidebar-content sidebar-content-open">
        <div className="sidebar-section session-list-section">
          <div className="sidebar-label">对话列表</div>
          <div className="session-list">
            {items.length === 0 ? (
              <div className="session-empty">暂无对话</div>
            ) : (
              items.map((item) => {
                const active = item.id === currentId;
                const editing = item.id === editingId;
                return (
                  <div key={item.id} className={`session-row ${active ? "session-row-active" : ""}`}>
                    {editing ? (
                      <form className="rename-form" onSubmit={submitRename}>
                        <input
                          value={draftTitle}
                          onChange={(event) => setDraftTitle(event.target.value)}
                          autoFocus
                        />
                        <button type="submit" disabled={pendingAction === "rename"}>
                          {pendingAction === "rename" ? "保存中..." : "保存"}
                        </button>
                        <button
                          type="button"
                          disabled={pendingAction === "rename"}
                          onClick={() => {
                            setEditingId(null);
                            setDraftTitle("");
                          }}
                        >
                          取消
                        </button>
                        {actionError ? <div className="rename-error">{actionError}</div> : null}
                      </form>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="session-title-button"
                          onClick={() => {
                            setMenuPlacement(null);
                            onSelect(item.id);
                          }}
                          disabled={streaming || loadingHistory}
                          title={item.title}
                        >
                          <span>{item.title}</span>
                          <small>{formatTime(item.updatedAt)}</small>
                        </button>
                        <div className="session-menu-wrap">
                          <button
                            type="button"
                            className="session-menu-button"
                            aria-label="更多操作"
                            aria-expanded={menuPlacement?.id === item.id}
                            onClick={(event) => toggleMenu(event, item)}
                            disabled={streaming}
                          >
                            ⋯
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
      </aside>
      {floatingMenu ? createPortal(floatingMenu, document.body) : null}
    </>
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
