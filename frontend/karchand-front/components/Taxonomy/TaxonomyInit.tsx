import { ProjectPrediction } from "@/interfaces/projectPrediction";
import { ProjectSettingsState, TaxonomyConfig } from "@/interfaces/taxonomy";



export function buildInitialSettings(
  taxonomy: TaxonomyConfig,
  suggested: ProjectPrediction["suggested_settings"]
): ProjectSettingsState {
  const enum_modifiers: Record<string, string> = {};
  for (const [key, field] of Object.entries(taxonomy?.enum_modifiers.fields)) {
    enum_modifiers[key] = field.values[0];
  }
  Object.assign(enum_modifiers, suggested?.enum_modifiers ?? {});

  const numeric_modifiers: Record<string, number> = {};
  for (const key of Object.keys(taxonomy?.numeric_modifiers.fields)) {
    numeric_modifiers[key] = suggested?.numeric_modifiers?.[key] ?? 0;
  }

  return {
    features: suggested?.features ?? [],
    application_types: suggested?.application_types ?? [],
    technology_modifiers: suggested?.technology_modifiers ?? [],
    boolean_modifiers: suggested?.boolean_modifiers ?? [],
    enum_modifiers,
    numeric_modifiers,
  };
}