import { SimilarProject } from "./similarProjects";

export interface ProjectPrediction {
  estimated_price_tomans: number;
  reasoning: string;
  suggested_settings: {
    features: string[];
    boolean_modifiers: string[];
    application_types: string[];
    technology_modifiers: string[];
    enum_modifiers?: Record<string, string>; // if API returns other enum fields too
    numeric_modifiers?: Record<string, number>;
  };
  similarProjects: SimilarProject[]
}