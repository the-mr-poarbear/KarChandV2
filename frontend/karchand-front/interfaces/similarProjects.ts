export interface SimilarProject {
  id: string;
  title: string;
  date: Date;
  description: string;
  timeline_days: number;
  actual_price_tomans: number;
  converted_price_tomans: number;
  converted_max_budget_tomans:number,
  converted_min_budget_tomans:number,
  max_budget:number,
  min_budget:number,
  project_link:string
}