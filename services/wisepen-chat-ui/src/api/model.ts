import type { ChatModel, ModelGroups } from "../types/chat";
import { apiFetch, readApiData } from "./client";

type ModelDto = {
  id: number;
  name: string;
  vendor: string;
  type: string;
  ratio: number;
  support_thinking: boolean;
  support_vision: boolean;
  is_default: boolean;
};

type ModelsResponseDto = {
  standard_models?: ModelDto[];
  advanced_models?: ModelDto[];
  other_models?: ModelDto[];
};

export async function listModels(): Promise<ModelGroups> {
  const response = await apiFetch("/chat/model/listModels");
  const data = await readApiData<ModelsResponseDto>(response, "加载模型列表失败");

  return {
    standard: (data.standard_models || []).map(mapModel),
    advanced: (data.advanced_models || []).map(mapModel),
    other: (data.other_models || []).map(mapModel),
  };
}

export function flattenModels(groups: ModelGroups): ChatModel[] {
  return [...groups.standard, ...groups.advanced, ...groups.other];
}

function mapModel(dto: ModelDto): ChatModel {
  return {
    id: dto.id,
    name: dto.name,
    vendor: dto.vendor,
    type: dto.type,
    ratio: dto.ratio,
    supportThinking: dto.support_thinking,
    supportVision: dto.support_vision,
    isDefault: dto.is_default,
  };
}
