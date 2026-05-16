import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { createPortal } from "react-dom";
import {
  clearCustomSearchProvider,
  getSearchProviderConfig,
  runtimeSelectionFromConfig,
  searchProviders,
  setCustomSearchProvider,
  setSearchProviderMode,
  verifyCustomSearchProvider,
} from "../api/searchProvider";
import type {
  SearchProviderConfig,
  SearchProviderMode,
  SearchProviderName,
  SearchProviderRuntimeSelection,
} from "../api/searchProvider";

type SearchProviderSelectorProps = {
  disabled?: boolean;
  onSelectionChange?: (selection: SearchProviderRuntimeSelection) => void;
};

const DEFAULT_PROVIDER: SearchProviderName = "serper";

const defaultConfig: SearchProviderConfig = {
  mode: "default",
  provider: null,
  keyPrefix4: null,
  keyLast4: null,
  status: "unset",
  lastVerifiedAt: null,
  lastErrorCode: null,
};

export function SearchProviderSelector({
  disabled = false,
  onSelectionChange,
}: SearchProviderSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [config, setConfig] = useState<SearchProviderConfig>(defaultConfig);
  const [draftMode, setDraftMode] = useState<SearchProviderMode>("default");
  const [provider, setProvider] = useState<SearchProviderName>(DEFAULT_PROVIDER);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [dropdownStyle, setDropdownStyle] = useState<CSSProperties>({});
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const emitMissingCustomCredential = useCallback(
    (nextProvider: SearchProviderName) => {
      onSelectionChange?.({
        mode: "custom",
        provider: nextProvider,
        useSavedKey: false,
      });
    },
    [onSelectionChange],
  );

  const emitCustomSelection = useCallback(
    (nextProvider: SearchProviderName, nextApiKey: string) => {
      const trimmedKey = nextApiKey.trim();
      if (trimmedKey) {
        onSelectionChange?.({
          mode: "custom",
          provider: nextProvider,
          useSavedKey: false,
          apiKey: trimmedKey,
        });
        return;
      }

      if (config.provider === nextProvider && config.keyLast4) {
        onSelectionChange?.({
          mode: "custom",
          provider: nextProvider,
          useSavedKey: true,
        });
        return;
      }

      emitMissingCustomCredential(nextProvider);
    },
    [config.keyLast4, config.provider, emitMissingCustomCredential, onSelectionChange],
  );

  useEffect(() => {
    let alive = true;

    getSearchProviderConfig()
      .then((nextConfig) => {
        if (!alive) {
          return;
        }
        setConfig(nextConfig);
        setDraftMode(nextConfig.mode);
        setProvider(nextConfig.provider ?? DEFAULT_PROVIDER);
        onSelectionChange?.(runtimeSelectionFromConfig(nextConfig));
      })
      .catch((error) => {
        if (alive) {
          setErrorText(error instanceof Error ? error.message : String(error));
        }
      });

    return () => {
      alive = false;
    };
  }, [onSelectionChange]);

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
      const width = Math.min(360, window.innerWidth - viewportPadding * 2);
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

  const run = async (action: () => Promise<SearchProviderConfig>) => {
    setBusy(true);
    setErrorText(null);
    try {
      const nextConfig = await action();
      setConfig(nextConfig);
      setDraftMode(nextConfig.mode);
      setProvider(nextConfig.provider ?? provider);
      onSelectionChange?.(runtimeSelectionFromConfig(nextConfig));
      return nextConfig;
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const selectMode = async (mode: SearchProviderMode) => {
    setDraftMode(mode);
    setErrorText(null);

    if (mode === "default") {
      await run(() => setSearchProviderMode("default"));
      return;
    }

    if (config.provider && config.keyLast4 && !apiKey.trim()) {
      await run(() => setSearchProviderMode("custom"));
      return;
    }

    emitCustomSelection(provider, apiKey);
  };

  const changeProvider = (nextProvider: SearchProviderName) => {
    setProvider(nextProvider);
    if (draftMode === "custom") {
      emitCustomSelection(nextProvider, apiKey);
    }
  };

  const changeApiKey = (nextApiKey: string) => {
    setApiKey(nextApiKey);
    if (draftMode === "custom") {
      emitCustomSelection(provider, nextApiKey);
    }
  };

  const saveCustomProvider = async (event: FormEvent) => {
    event.preventDefault();
    const trimmedKey = apiKey.trim();
    if (!trimmedKey) {
      setErrorText("请输入 API Key");
      return;
    }

    const nextConfig = await run(() => setCustomSearchProvider(provider, trimmedKey));
    if (nextConfig) {
      setApiKey("");
    }
  };

  const clearCustomProvider = async () => {
    const nextConfig = await run(clearCustomSearchProvider);
    if (nextConfig) {
      setApiKey("");
      setProvider(DEFAULT_PROVIDER);
    }
  };

  const verifyCustomProvider = async () => {
    await run(verifyCustomSearchProvider);
  };

  const draftProviderLabel =
    searchProviders.find((item) => item.value === provider)?.label || "未配置";
  const triggerValue =
    draftMode === "custom" ? `Custom · ${draftProviderLabel}` : "Default";
  const maskedKey =
    config.keyPrefix4 && config.keyLast4
      ? `${config.keyPrefix4}••••${config.keyLast4}`
      : "未保存";

  return (
    <div className="search-provider-selector-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="search-provider-trigger"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        title={triggerValue}
      >
        <span className="search-provider-label">搜索</span>
        <span className="search-provider-value">{triggerValue}</span>
        <span className={`search-provider-arrow ${isOpen ? "open" : ""}`}>▼</span>
      </button>

      {isOpen &&
        createPortal(
          <div
            className="search-provider-panel"
            ref={dropdownRef}
            style={dropdownStyle}
          >
            <div className="search-provider-panel-header">
              <span className="search-provider-panel-title">搜索源</span>
              <span className={`search-provider-status status-${config.status}`}>
                {formatStatus(config.status)}
              </span>
            </div>

            <div className="search-mode-segmented" role="group" aria-label="搜索模式">
              <button
                type="button"
                className={draftMode === "default" ? "active" : ""}
                onClick={() => void selectMode("default")}
                disabled={busy}
              >
                Default
              </button>
              <button
                type="button"
                className={draftMode === "custom" ? "active" : ""}
                onClick={() => void selectMode("custom")}
                disabled={busy}
              >
                Custom
              </button>
            </div>

            {draftMode === "custom" ? (
              <form className="custom-provider-card" onSubmit={saveCustomProvider}>
                <label className="custom-provider-row">
                  <span>供应商</span>
                  <select
                    value={provider}
                    onChange={(event) =>
                      changeProvider(event.target.value as SearchProviderName)
                    }
                    disabled={busy}
                  >
                    {searchProviders.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="custom-provider-row">
                  <span>API Key</span>
                  <input
                    value={apiKey}
                    onChange={(event) => changeApiKey(event.target.value)}
                    type="password"
                    autoComplete="off"
                    placeholder={maskedKey}
                    disabled={busy}
                  />
                </label>

                <div className="custom-provider-meta">
                  <span>{maskedKey}</span>
                  {config.lastErrorCode ? <span>{config.lastErrorCode}</span> : null}
                </div>

                <div className="custom-provider-actions">
                  <button type="submit" disabled={busy || !apiKey.trim()}>
                    保存
                  </button>
                  <button
                    type="button"
                    onClick={() => void verifyCustomProvider()}
                    disabled={busy || !config.provider || !config.keyLast4}
                  >
                    验证
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void clearCustomProvider()}
                    disabled={busy || !config.provider}
                  >
                    清除
                  </button>
                </div>
              </form>
            ) : null}

            {errorText ? (
              <div className="search-provider-error">{errorText}</div>
            ) : null}
          </div>,
          document.body,
        )}
    </div>
  );
}

function formatStatus(status: string): string {
  if (status === "valid") {
    return "valid";
  }
  if (status === "untested") {
    return "untested";
  }
  if (status === "invalid") {
    return "invalid";
  }
  if (status === "quota_exhausted") {
    return "quota";
  }
  if (status === "rate_limited") {
    return "limited";
  }
  if (status === "provider_error") {
    return "error";
  }
  return "unset";
}
