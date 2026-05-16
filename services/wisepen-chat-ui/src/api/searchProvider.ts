import { apiFetch, readApiData } from "./client";

export type SearchProviderMode = "default" | "custom";

export type SearchProviderName =
  | "serper"
  | "tavily"
  | "brave"
  | "serpapi"
  | "exa"
  | "perplexity";

export type SearchProviderConfig = {
  mode: SearchProviderMode;
  provider: SearchProviderName | null;
  keyPrefix4: string | null;
  keyLast4: string | null;
  status: string;
  lastVerifiedAt: string | null;
  lastErrorCode: string | null;
};

export type SearchProviderRuntimeSelection =
  | { mode: "default" }
  | {
      mode: "custom";
      provider: SearchProviderName | null;
      useSavedKey: boolean;
      apiKey?: string | null;
    };

type SearchProviderConfigDto = {
  mode: SearchProviderMode;
  provider?: SearchProviderName | null;
  key_prefix4?: string | null;
  key_last4?: string | null;
  status: string;
  last_verified_at?: string | null;
  last_error_code?: string | null;
};

export const searchProviders: Array<{
  value: SearchProviderName;
  label: string;
}> = [
  { value: "serper", label: "Serper" },
  { value: "tavily", label: "Tavily" },
  { value: "brave", label: "Brave" },
  { value: "serpapi", label: "SerpAPI" },
  { value: "exa", label: "Exa" },
  { value: "perplexity", label: "Perplexity" },
];

export async function getSearchProviderConfig(): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/get");
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "加载搜索源配置失败",
  );
  return mapConfig(data);
}

export async function setSearchProviderMode(
  mode: SearchProviderMode,
): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/setMode", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "设置搜索模式失败",
  );
  return mapConfig(data);
}

export async function setCustomSearchProvider(
  provider: SearchProviderName,
  apiKey: string,
): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/setCustomProvider", {
    method: "POST",
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "保存自定义搜索源失败",
  );
  return mapConfig(data);
}

export async function clearCustomSearchProvider(): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/clearCustomProvider", {
    method: "POST",
  });
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "清除自定义搜索源失败",
  );
  return mapConfig(data);
}

export async function verifyCustomSearchProvider(): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/verify", {
    method: "POST",
  });
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "验证自定义搜索源失败",
  );
  return mapConfig(data);
}

export function runtimeSelectionFromConfig(
  config: SearchProviderConfig,
): SearchProviderRuntimeSelection {
  if (config.mode === "custom" && config.provider && config.keyLast4) {
    return {
      mode: "custom",
      provider: config.provider,
      useSavedKey: true,
    };
  }
  return { mode: "default" };
}

function mapConfig(dto: SearchProviderConfigDto): SearchProviderConfig {
  return {
    mode: dto.mode,
    provider: dto.provider ?? null,
    keyPrefix4: dto.key_prefix4 ?? null,
    keyLast4: dto.key_last4 ?? null,
    status: dto.status,
    lastVerifiedAt: dto.last_verified_at ?? null,
    lastErrorCode: dto.last_error_code ?? null,
  };
}
