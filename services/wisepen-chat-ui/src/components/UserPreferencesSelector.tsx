import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  allowedLocales,
  allowedTimezones,
  getUserPreferences,
  updateUserLocale,
  updateUserTimezone,
} from "../api/userPreferences";
import type { UserLocale, UserPreferences } from "../api/userPreferences";

type UserPreferencesSelectorProps = {
  disabled?: boolean;
};

const DEFAULT_PREFERENCES: UserPreferences = {
  timezone: "Asia/Shanghai",
  locale: "zh-CN",
};

const localeLabels: Record<UserLocale, string> = {
  "zh-CN": "简体中文",
  "zh-TW": "繁体中文（台湾）",
  "zh-HK": "繁体中文（香港）",
  "en-US": "English (US)",
  "en-GB": "English (UK)",
  "ja-JP": "日本語",
  "ko-KR": "한국어",
};

export function UserPreferencesSelector({
  disabled = false,
}: UserPreferencesSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [preferences, setPreferences] =
    useState<UserPreferences>(DEFAULT_PREFERENCES);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [dropdownStyle, setDropdownStyle] = useState<CSSProperties>({});
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let alive = true;

    getUserPreferences()
      .then((nextPreferences) => {
        if (alive) {
          setPreferences(nextPreferences);
        }
      })
      .catch((error: unknown) => {
        if (alive) {
          setErrorText(error instanceof Error ? error.message : String(error));
        }
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
      }
    };

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
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

  const changeTimezone = async (timezone: string) => {
    setBusy(true);
    setErrorText(null);

    try {
      setPreferences(await updateUserTimezone(timezone));
    } catch (error: unknown) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const changeLocale = async (locale: UserLocale) => {
    setBusy(true);
    setErrorText(null);

    try {
      setPreferences(await updateUserLocale(locale));
    } catch (error: unknown) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="user-preferences-selector-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="user-preferences-trigger"
        onClick={() => {
          if (!disabled) {
            setIsOpen((current) => !current);
          }
        }}
        disabled={disabled}
        title={`${preferences.locale} · ${preferences.timezone}`}
      >
        <span className="user-preferences-label">偏好</span>
        <span className="user-preferences-value">{preferences.locale}</span>
        <span className={`user-preferences-arrow ${isOpen ? "open" : ""}`}>
          ▼
        </span>
      </button>

      {isOpen
        ? createPortal(
            <div
              ref={dropdownRef}
              className="user-preferences-panel"
              style={dropdownStyle}
            >
              <div className="user-preferences-panel-header">
                <span className="user-preferences-panel-title">用户偏好</span>
              </div>

              <label className="user-preferences-row">
                <span>时区</span>
                <select
                  value={preferences.timezone}
                  onChange={(event) => void changeTimezone(event.target.value)}
                  disabled={busy}
                >
                  {allowedTimezones.map((timezone) => (
                    <option key={timezone} value={timezone}>
                      {timezone}
                    </option>
                  ))}
                </select>
              </label>

              <label className="user-preferences-row">
                <span>语言/区域</span>
                <select
                  value={preferences.locale}
                  onChange={(event) =>
                    void changeLocale(event.target.value as UserLocale)
                  }
                  disabled={busy}
                >
                  {allowedLocales.map((locale) => (
                    <option key={locale} value={locale}>
                      {localeLabels[locale]}
                    </option>
                  ))}
                </select>
              </label>

              {errorText ? (
                <div className="user-preferences-error">{errorText}</div>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
