import {apiFetch, readApiData} from "./client";

export const allowedTimezones = [
  "Asia/Shanghai",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Asia/Tokyo",
  "Asia/Seoul",
] as const;

export const allowedLocales = [
  "zh-CN",
  "zh-TW",
  "zh-HK",
  "en-US",
  "en-GB",
  "ja-JP",
  "ko-KR",
] as const;

export type UserLocale = (typeof allowedLocales)[number];

export type UserPreferences = {
  timezone: string;
  locale: UserLocale;
};

type UserPreferencesDto = {
  timezone: string;
  locale: UserLocale;
};

export async function getUserPreferences(): Promise<UserPreferences> {
  const response = await apiFetch("/chat/user/preferences");
  return await readApiData<UserPreferencesDto>(response, "加载用户偏好失败");
}

export async function updateUserTimezone(
  timezone: string,
): Promise<UserPreferences> {
  const response = await apiFetch("/chat/user/preferences/timezone", {
    method: "POST",
    body: JSON.stringify({ timezone }),
  });
  return await readApiData<UserPreferencesDto>(response, "更新时区失败");
}

export async function updateUserLocale(
  locale: UserLocale,
): Promise<UserPreferences> {
  const response = await apiFetch("/chat/user/preferences/locale", {
    method: "POST",
    body: JSON.stringify({ locale }),
  });
  return await readApiData<UserPreferencesDto>(response, "更新语言区域失败");
}
