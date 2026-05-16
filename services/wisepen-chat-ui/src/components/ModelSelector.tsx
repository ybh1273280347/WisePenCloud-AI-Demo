import { useState, useRef, useEffect } from "react";
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { ModelGroups, ChatModel } from "../types/chat";

type ModelSelectorProps = {
  modelName?: string;
  modelGroups: ModelGroups;
  selectedModelId: number | null;
  onModelChange: (modelId: number | null) => void;
  disabled?: boolean;
};

export function ModelSelector({
  modelName,
  modelGroups,
  selectedModelId,
  onModelChange,
  disabled = false,
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isAutoMode, setIsAutoMode] = useState(selectedModelId === null);
  const [dropdownStyle, setDropdownStyle] = useState<CSSProperties>({});
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(target) &&
        triggerRef.current &&
        !triggerRef.current.contains(target)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const updateDropdownPosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }

      const gap = 8;
      const viewportPadding = 12;
      const width = Math.min(320, window.innerWidth - viewportPadding * 2);
      setDropdownStyle({
        position: "fixed",
        top: rect.bottom + gap,
        left: Math.min(
          Math.max(viewportPadding, rect.right - width),
          window.innerWidth - width - viewportPadding,
        ),
        width,
      });
    };

    updateDropdownPosition();
    window.addEventListener("resize", updateDropdownPosition);
    window.addEventListener("scroll", updateDropdownPosition, true);

    return () => {
      window.removeEventListener("resize", updateDropdownPosition);
      window.removeEventListener("scroll", updateDropdownPosition, true);
    };
  }, [isOpen]);

  useEffect(() => {
    setIsAutoMode(selectedModelId === null);
  }, [selectedModelId]);

  const handleAutoModeToggle = () => {
    setIsAutoMode(!isAutoMode);
    if (!isAutoMode) {
      onModelChange(null);
    } else {
      const firstModel = [...modelGroups.standard, ...modelGroups.advanced, ...modelGroups.other][0];
      if (firstModel) {
        onModelChange(firstModel.id);
      }
    }
  };

  const handleModelSelect = (model: ChatModel) => {
    onModelChange(model.id);
    setIsOpen(false);
    setIsAutoMode(false);
  };

  const allModels = [
    { label: "标准", models: modelGroups.standard },
    { label: "高级", models: modelGroups.advanced },
    { label: "其他", models: modelGroups.other },
  ].filter((group) => group.models.length > 0);

  return (
    <div className="model-selector-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="model-selector-trigger"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        title={modelName || "后端默认模型"}
      >
        <span className="model-selector-label">模型</span>
        <span className="model-selector-value">
          {selectedModelId === null ? "Auto Mode" : modelName || "未选择"}
        </span>
        <span className={`model-selector-arrow ${isOpen ? "open" : ""}`}>▼</span>
      </button>

      {isOpen &&
        createPortal(
        <div className="model-dropdown-panel" ref={dropdownRef} style={dropdownStyle}>
          <div className="model-dropdown-header">
            <span className="model-dropdown-title">选择模型</span>
            <button
              type="button"
              className={`auto-mode-toggle ${isAutoMode ? "active" : ""}`}
              onClick={handleAutoModeToggle}
            >
              <span className="auto-mode-label">Auto Mode</span>
              <span className="auto-mode-switch">
                <span className={`auto-mode-thumb ${isAutoMode ? "on" : ""}`} />
              </span>
            </button>
          </div>

          <div className="model-dropdown-content">
            {allModels.map((group) => (
              <div key={group.label} className="model-group">
                <div className="model-group-label">{group.label}</div>
                <div className="model-group-list">
                  {group.models.map((model) => (
                    <button
                      type="button"
                      key={model.id}
                      className={`model-option ${selectedModelId === model.id ? "selected" : ""}`}
                      onClick={() => handleModelSelect(model)}
                    >
                      <span className="model-option-check">
                        {selectedModelId === model.id && "✓"}
                      </span>
                      <span className="model-option-name">{model.name}</span>
                      {model.vendor && <span className="model-option-vendor">· {model.vendor}</span>}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
