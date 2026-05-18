import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type {
  SearchProviderConfig,
  SearchProviderMode,
  SearchProviderName,
} from "../api/searchProvider";
import {
  clearCustomSearchProvider,
  getSearchProviderConfig,
  searchProviders,
  setCustomSearchProvider,
  setSearchProviderMode,
  verifyCustomSearchProvider,
} from "../api/searchProvider";

type SearchProviderSelectorProps = {
  disabled?: boolean;
};

type VerifyResult = {
  success: boolean;
  message: string;
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

const SEARCH_PROVIDER_STATUS_LABELS: Record<string, string> = {
  unset: "unset",
  valid: "valid",
  untested: "untested",
  invalid: "invalid",
  quota_exhausted: "quota",
  rate_limited: "limited",
  provider_error: "error",
};

export function SearchProviderSelector({
  disabled = false,
}: SearchProviderSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [providerMenuOpen, setProviderMenuOpen] = useState(false);
  const [config, setConfig] = useState<SearchProviderConfig>(defaultConfig);
  const [draftMode, setDraftMode] = useState<SearchProviderMode>("default");
  const [provider, setProvider] = useState<SearchProviderName>(DEFAULT_PROVIDER);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [dropdownStyle, setDropdownStyle] = useState<CSSProperties>({});

  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const providerStatus = config.status;
  const providerStatusLabel =
    SEARCH_PROVIDER_STATUS_LABELS[providerStatus] ?? "unset";

  const draftProviderLabel =
    searchProviders.find((item) => item.value === provider)?.label ?? "未配置";

  const triggerValue =
    draftMode === "custom" ? `Custom · ${draftProviderLabel}` : "Default";

  const maskedKey =
    config.keyPrefix4 && config.keyLast4
      ? `${config.keyPrefix4}••••${config.keyLast4}`
      : "未保存";

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
      })
      .catch((error: unknown) => {
        if (!alive) {
          return;
        }

        setErrorText(error instanceof Error ? error.message : String(error));
      });

    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target;

      if (!(target instanceof Node)) {
        return;
      }

      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(target) &&
        triggerRef.current &&
        !triggerRef.current.contains(target)
      ) {
        setIsOpen(false);
        setProviderMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setProviderMenuOpen(false);
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

  const run = async (
    action: () => Promise<SearchProviderConfig>,
  ): Promise<SearchProviderConfig | null> => {
    setBusy(true);
    setErrorText(null);
    setVerifyResult(null);

    try {
      const nextConfig = await action();

      setConfig(nextConfig);
      setDraftMode(nextConfig.mode);
      setProvider(nextConfig.provider ?? provider);

      return nextConfig;
    } catch (error: unknown) {
      setErrorText(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const selectMode = async (mode: SearchProviderMode) => {
    setDraftMode(mode);
    setErrorText(null);
    setVerifyResult(null);
    setProviderMenuOpen(false);

    if (mode === "default") {
      await run(() => setSearchProviderMode("default"));
      return;
    }

    if (config.provider && config.keyLast4 && !apiKey) {
      await run(() => setSearchProviderMode("custom"));
      return;
    }

    if (!apiKey) {
      setErrorText("请保存自定义搜索源 API Key 后启用");
    }
  };

  const changeProvider = (nextProvider: SearchProviderName) => {
    setProvider(nextProvider);
    setProviderMenuOpen(false);

    setErrorText(null);
  };

  const changeApiKey = (nextApiKey: string) => {
    setApiKey(nextApiKey);
  };

  const saveCustomProvider = async () => {
    if (!apiKey) {
      setErrorText("请输入 API Key");
      return;
    }

    const nextConfig = await run(() =>
      setCustomSearchProvider(provider, apiKey),
    );

    if (nextConfig) {
      setApiKey("");
    }
  };

  const clearCustomProvider = async () => {
    const nextConfig = await run(clearCustomSearchProvider);

    if (nextConfig) {
      setApiKey("");
      setProvider(DEFAULT_PROVIDER);
      setProviderMenuOpen(false);
    }
  };

  const handleVerify = async () => {
    setVerifyResult(null);
    setErrorText(null);
    setBusy(true);

    try {
      const nextConfig = await verifyCustomSearchProvider();

      setConfig(nextConfig);
      setDraftMode(nextConfig.mode);
      setProvider(nextConfig.provider ?? provider);

      if (nextConfig.status === "valid") {
        setVerifyResult({ success: true, message: "验证成功" });
      } else if (nextConfig.lastErrorCode) {
        setVerifyResult({ success: false, message: nextConfig.lastErrorCode });
      } else {
        setVerifyResult({ success: false, message: "验证失败" });
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);

      setErrorText(message);
      setVerifyResult({ success: false, message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="search-provider-selector-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="search-provider-trigger"
        onClick={() => {
          if (!disabled) {
            setIsOpen((current) => !current);
          }
        }}
        disabled={disabled}
        title={triggerValue}
      >
        <span className="search-provider-label">搜索</span>
        <span className="search-provider-value">{triggerValue}</span>
        <span className={`search-provider-arrow ${isOpen ? "open" : ""}`}>
          ▼
        </span>
      </button>

      {isOpen
        ? createPortal(
            <div
              ref={dropdownRef}
              className="search-provider-panel"
              style={dropdownStyle}
            >
              <div className="search-provider-panel-header">
                <span className="search-provider-panel-title">搜索源</span>
                <span
                  className={`search-provider-status status-${providerStatus}`}
                >
                  {providerStatusLabel}
                </span>
              </div>

              <div
                className="search-mode-segmented"
                role="group"
                aria-label="搜索模式"
              >
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
                <div className="custom-provider-card">
                  <label className="custom-provider-row">
                    <span>供应商</span>

                    <div className="provider-custom-select">
                      <button
                        type="button"
                        className={`provider-custom-trigger ${
                          providerMenuOpen ? "open" : ""
                        }`}
                        onClick={() => {
                          if (!busy) {
                            setProviderMenuOpen((current) => !current);
                          }
                        }}
                        disabled={busy}
                        aria-haspopup="listbox"
                        aria-expanded={providerMenuOpen}
                      >
                        <span className="provider-custom-value">
                          {draftProviderLabel}
                        </span>
                        <span
                          className={`provider-custom-arrow ${
                            providerMenuOpen ? "open" : ""
                          }`}
                        >
                          ╲╱
                        </span>
                      </button>

                      {providerMenuOpen ? (
                        <div
                          className="provider-custom-menu"
                          role="listbox"
                          aria-label="搜索供应商"
                        >
                          {searchProviders.map((item) => {
                            const selected = item.value === provider;

                            return (
                              <div
                                key={item.value}
                                className={`provider-custom-option ${
                                  selected ? "selected" : ""
                                }`}
                                role="option"
                                aria-selected={selected}
                                tabIndex={0}
                                onClick={() =>
                                  changeProvider(
                                    item.value as SearchProviderName,
                                  )
                                }
                                onKeyDown={(event) => {
                                  if (
                                    event.key === "Enter" ||
                                    event.key === " "
                                  ) {
                                    event.preventDefault();
                                    changeProvider(
                                      item.value as SearchProviderName,
                                    );
                                  }

                                  if (event.key === "Escape") {
                                    setProviderMenuOpen(false);
                                  }
                                }}
                              >
                                <span>{item.label}</span>
                                {selected ? (
                                  <span className="provider-custom-check">
                                    ✓
                                  </span>
                                ) : null}
                              </div>
                            );
                          })}
                        </div>
                      ) : null}
                    </div>
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

                  {config.lastErrorCode ? (
                    <div className="custom-provider-meta">
                      <span>{config.lastErrorCode}</span>
                    </div>
                  ) : null}

                  <div className="custom-provider-actions">
                    <button
                      type="button"
                      onClick={() => void saveCustomProvider()}
                      disabled={busy || !apiKey}
                    >
                      保存
                    </button>

                    <button
                      type="button"
                      onClick={() => void handleVerify()}
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

                  {verifyResult || (errorText && draftMode === "custom") ? (
                    <div
                      className={`search-provider-verify ${
                        verifyResult
                          ? verifyResult.success
                            ? "verify-success"
                            : "verify-error"
                          : "verify-error"
                      }`}
                    >
                      {verifyResult
                        ? `${verifyResult.success ? "✓" : "✗"} ${
                            verifyResult.message
                          }`
                        : errorText}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
