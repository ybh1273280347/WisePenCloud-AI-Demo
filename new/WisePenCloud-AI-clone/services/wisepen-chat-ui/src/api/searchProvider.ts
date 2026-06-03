import { apiFetch, readApiData } from "./client";

export type SearchProviderMode = "default" | "custom";

export type SearchProviderName =
  | "serper"
  | "tavily"
  | "brave"
  | "serpapi"
  | "exa"
  | "perplexity"
  | "anysearch";

export type SearchProviderConfig = {
  mode: SearchProviderMode;
  provider: SearchProviderName | null;
  maskedKey: string | null;
  isValid: boolean;
};

type SearchProviderConfigDto = {
  provider_mode: SearchProviderMode;
  provider?: SearchProviderName | null;
  masked_key?: string | null;
  is_valid?: boolean;
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
    { value: "anysearch", label: "AnySearch" },
  ];

export async function getSearchProviderConfig(): Promise<SearchProviderConfig> {
  const response = await apiFetch("/chat/searchProvider/getConfig");
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
  const response = await apiFetch("/chat/searchProvider/verifyProvider", {
    method: "POST",
  });
  const data = await readApiData<SearchProviderConfigDto>(
    response,
    "验证自定义搜索源失败",
  );
  return mapConfig(data);
}

function mapConfig(dto: SearchProviderConfigDto): SearchProviderConfig {
  const mode = normalizeMode(dto.provider_mode);
  return {
    mode,
    provider: dto.provider ?? null,
    maskedKey: dto.masked_key ?? null,
    isValid: dto.is_valid ?? false,
  };
}

function normalizeMode(mode: SearchProviderConfigDto["provider_mode"]): SearchProviderMode {
  return String(mode || "default").toLowerCase() === "custom" ? "custom" : "default";
}
