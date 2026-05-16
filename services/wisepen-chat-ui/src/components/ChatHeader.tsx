import type { ConnectionStatus, ModelGroups } from "../types/chat";
import type { SearchProviderRuntimeSelection } from "../api/searchProvider";
import { ModelSelector } from "./ModelSelector";
import { SearchProviderSelector } from "./SearchProviderSelector";

type ChatHeaderProps = {
  title: string;
  modelName?: string;
  modelGroups: ModelGroups;
  selectedModelId: number | null;
  onModelChange: (modelId: number | null) => void;
  onSearchProviderChange: (selection: SearchProviderRuntimeSelection) => void;
  connectionStatus: ConnectionStatus;
  streaming: boolean;
  fileCount: number;
  messageCount: number;
};

export function ChatHeader({
  title,
  modelName,
  modelGroups,
  selectedModelId,
  onModelChange,
  onSearchProviderChange,
  connectionStatus,
  streaming,
  fileCount,
  messageCount,
}: ChatHeaderProps) {
  const statusLabel =
    streaming ? "生成中" : connectionStatus === "connected" ? "已连接" : connectionStatus === "connecting" ? "连接中" : "异常";

  const modelCount = (groups: ModelGroups): number => {
    return groups.standard.length + groups.advanced.length + groups.other.length;
  };

  return (
    <header className="chat-header">
      <div className="chat-header-main">
        <div className="chat-header-eyebrow">当前对话</div>
        <h1>{title || "新对话"}</h1>
      </div>

      <div className="chat-header-meta" aria-label="对话状态">
        <ModelSelector
          modelName={modelName}
          modelGroups={modelGroups}
          selectedModelId={selectedModelId}
          onModelChange={onModelChange}
          disabled={streaming || modelCount(modelGroups) === 0}
        />
        <SearchProviderSelector
          disabled={streaming}
          onSelectionChange={onSearchProviderChange}
        />
        <span className={`header-pill connection-pill connection-pill-${connectionStatus}`}>
          <span className="status-dot" />
          {statusLabel}
        </span>
        <span className="header-pill">{messageCount} 条消息</span>
        <span className="header-pill">{fileCount} 个文件</span>
      </div>
    </header>
  );
}
