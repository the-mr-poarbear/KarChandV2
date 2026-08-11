'use client'

import { ProjectPrediction } from "@/interfaces/projectPrediction";
import SimilarProjectCard from "./SimilarProjectCard";
import { SettingsSummary } from "../Settings/settings-summary";
import { ProjectSettingsState, TaxonomyConfig } from "@/interfaces/taxonomy";
import { useEffect, useState } from "react";
import taxonomy from "@/data/taxonomy/taxonomy.json";
import { buildInitialSettings } from "../Taxonomy/TaxonomyInit";
import { SimilarProject } from "@/interfaces/similarProjects";


interface Props{
    ready:boolean;
    result: ProjectPrediction|undefined
    similarProjects:SimilarProject[]
}
const Result = ({ready,result ,similarProjects}:Props) => {
    const [settings, setSettings] = useState<ProjectSettingsState | null>(null);

    useEffect(()=>{
        setSettings(buildInitialSettings(taxonomy as TaxonomyConfig, result?.suggested_settings));
    },[result])
    return (
        <div id="result" className={`grid grid-cols-5 gap-4 w-full h-screen ${ready?"opacity-100 ":"opacity-0"} transition-all duration-1000 relative`}>
            <div className="border-2 col-span-2  border-border space-y-8 bg-white rounded-xl p-8 ">
                <div className="space-y-3">
                    <h2>تخمین هوش مصنوعی</h2>
                    <div className="border-border bg-white rounded-xl p-8 border-2 gap-3 w-full flex justify-center items-center">
                        <p className="text-4xl font-bold">{result?.estimated_price_tomans} 
                            <span className="text-primary font-semibold text-lg px-1">تومن</span>
                        </p>
                    </div>
                </div>

                <div className="space-y-3">
                    <h2>توضیحات</h2>
                    <div className="border-border overflow-auto max-h-100  bg-white rounded-xl gap-3 w-full ">
                        <p className="text-lg">{result?.reasoning} </p>
                    </div>
                </div>
            </div>

            <div className="space-y-3 col-span-3 ">
                <div className="border-2 border-border bg-white rounded-xl p-8">
                    <h2>تنظیمات</h2>
                        <SettingsSummary
                            taxonomy={taxonomy as TaxonomyConfig}
                            settings={settings}
                            onChange={setSettings}
                            />
                </div>

                <div className="border-2 border-border bg-white space-y-3 rounded-xl p-8">
                    <h2>پروژه های مشابه</h2>
                    {similarProjects?.map((project)=>
                        <SimilarProjectCard project={project} key={project.id} />
                    )}
                </div>
            </div>

        </div>
    )
}

export default Result