// lib/taxonomy-types.ts

export interface TaxonomyConfig {
  features: { values: string[] };
  application_types: { values: string[]; definitions?: Record<string, string> };
  technology_modifiers: { values: string[] };
  numeric_modifiers: { fields: Record<string, string> };
  boolean_modifiers: { fields: Record<string, string> };
  enum_modifiers: {
    fields: Record<string, { values: string[]; description: string }>;
  };
}

export interface ProjectSettingsState {
  features: string[];
  application_types: string[];
  technology_modifiers: string[];
  boolean_modifiers: string[];
  enum_modifiers: Record<string, string>;
  numeric_modifiers: Record<string, number>;
}