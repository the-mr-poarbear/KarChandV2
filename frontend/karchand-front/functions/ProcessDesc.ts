import { APIURL } from "@/data/consts";
import { ProjectPrediction } from "@/interfaces/projectPrediction";
import { postFetch, updateFetch } from "@/lib/fetch";
import { toast } from "sonner";


// const mock:ProjectPrediction = {
//     estimated_price_tomans: 255000000,

//   "reasoning": "The project is a small WordPress website for a cafe/restaurant with 2-5 pages. It requires a blog, SEO, SSL, Google Maps integration, and a CMS (provided by WordPress). As it uses a ready-made WordPress template, most features like Content Management are pre-installed. The core work involves setup, SEO, and adding a blog/map.",
//   "suggested_settings": {
//     "features": [
//       "SEO",
//       "Map & Geolocation Integration"
//     ],
//     "boolean_modifiers": [
//       "web"
//     ],
//     "application_types": [
//       "website"
//     ],
//     "technology_modifiers": [
//       "WordPress"
//     ],
//     "enum_modifiers": {
//       "project_type": "new",
//       "ai_level": "none",
//       "business_logic": "low",
//       "data_volume": "small",
//       "integration_complexity": "low",
//       "ui_complexity": "low",
//       "algorithmic_complexity": "low"
//     },
//     "numeric_modifiers": {
//       "external_api_count": 1,
//       "estimated_screens": 5,
//       "estimated_entities": 1
//     }
//   },

//     // similarProjects: [
//     //     {
//     //     id: "proj_1a",
//     //     title: "React Frontend Project",
//     //     date: "2026/02/03",
//     //     description: "A comprehensive web interface built with modern component libraries. The system required custom hooks for data fetching and a highly modular architecture for future scaling.",
//     //     timeline_days: 30,
//     //     actual_price_tomans: 1000000,
//     //     converted_price_tomans: 1905000,
//     //     project_link:"sth"
//     //     },
//     //     {
//     //     id: "proj_2b",
//     //     title: "NILI Internal Dashboard",
//     //     date: "2026/01/15",
//     //     description: "An administrative panel designed to manage user permissions and visualize activity metrics. Included complex data grids and chart integrations.",
//     //     timeline_days: 45,
//     //     actual_price_tomans: 2200000,
//     //     converted_price_tomans: 3100000,
//     //     project_link:"sth"
//     //     }
//     // ]
// }


export async function ProcessDesc(prompt: string) {

    const result = await postFetch(
        APIURL + "extract_taxonomy",
        JSON.stringify({
        "query":prompt
        })
    );



    const jsonResult = await result.json();

    if (result.ok) {
        toast.success("نتایج شما آماده است", {
        });
        return jsonResult;
    } else {
        // Handle different error status codes
        switch (result.status) {
        case 400:
            toast.error("خطای درخواست", {
            description:
                jsonResult.message || "مقدار ورودی نامعتبر است.",
            });
            break;
        case 401:
            toast.error("عدم احراز هویت", {
            description: "مقدار ورودی اشتباه است.",
            });
            break;
        case 403:
            toast.error("مقدار ورودی اشتباه است", {
            description: "لطفاً اطلاعات ورودی خود را بررسی کنید",
            });
            break;

        case 404:
            toast.error("api یافت نشد", {
            description: "apiی با این مشخصات وجود ندارد.",
            });
            break;
        case 409:
            toast.error("حساب قفل شده", {
            description: "حساب شما به دلیل تلاش‌های ناموفق متعدد قفل شده است.",
            });
            break;
        case 500:
            toast.error("خطای سرور", {
            description: "خطایی در سرور رخ داده است. لطفاً بعداً تلاش کنید.",
            });
            break;
        default:
            toast.error("خطای ناشناخته", {
            description:
                jsonResult.error || "خطای نامشخصی در هنگام ورود رخ داده است.",
            });
        }
        throw new Error(jsonResult.error || "خطای نامشخص");
    }
}
