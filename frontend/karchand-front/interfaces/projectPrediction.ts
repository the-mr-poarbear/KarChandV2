import { SimilarProject } from "./similarProjects";
import { ProjectSettingsState } from "./taxonomy";

export interface ProjectPrediction {
  estimated_price: number;
  reasoning: string;
  suggested_settings: ProjectSettingsState
}
// similarProjects: SimilarProject[]