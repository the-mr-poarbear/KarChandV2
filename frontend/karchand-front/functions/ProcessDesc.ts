import { APIURL } from "@/data/consts";
import { ProjectPrediction } from "@/interfaces/projectPrediction";
import { postFetch, updateFetch } from "@/lib/fetch";
import { toast } from "sonner";


const mock:ProjectPrediction = {
    estimated_price_tomans: 255000000,
    reasoning: "The requested application requires extensive frontend architecture, including state management, custom routing, and external API integrations. The need for real-time updates and complex user authorization significantly increases the base scope.",
    suggested_settings: {
      features: ["Authentication", "Dashboard", "Settings & Configuration", "External API Integration", "Real-time Updates"],
      enum_modifiers:{"project_type":"new"},
      boolean_modifiers: ["web", "real_time"],
      application_types: ["web_application", "api_backend"],
      technology_modifiers: ["Custom Development"]
    },
    similarProjects: [
        {
        id: "proj_1a",
        title: "React Frontend Project",
        date: "2026/02/03",
        description: "A comprehensive web interface built with modern component libraries. The system required custom hooks for data fetching and a highly modular architecture for future scaling.",
        timeline_days: 30,
        actual_price_tomans: 1000000,
        converted_price_tomans: 1905000
        },
        {
        id: "proj_2b",
        title: "NILI Internal Dashboard",
        date: "2026/01/15",
        description: "An administrative panel designed to manage user permissions and visualize activity metrics. Included complex data grids and chart integrations.",
        timeline_days: 45,
        actual_price_tomans: 2200000,
        converted_price_tomans: 3100000,
        graph_nodes: 450 
        }
    ]
}


export async function ProcessDesc(prompt: string) {
    return mock
    const result = await postFetch(
        APIURL + "process_page",
        JSON.stringify({
        prompt,
        })
    );



    const jsonResult = await result.json();

    if (result.ok) {
        toast.success("ورود با موفقیت انجام شد", {
        description: "شما با موفقیت به سیستم وارد شدید",
        });
        return jsonResult;
    } else {
        // Handle different error status codes
        switch (result.status) {
        case 400:
            toast.error("خطای درخواست", {
            description:
                jsonResult.message || "نام کاربری یا رمز عبور نامعتبر است.",
            });
            break;
        case 401:
            toast.error("عدم احراز هویت", {
            description: "نام کاربری یا رمز عبور اشتباه است.",
            });
            break;
        case 403:
            toast.error("نام کاربری یا رمز عبور اشتباه است", {
            description: "لطفاً اطلاعات ورود خود را بررسی کنید",
            });
            break;

        case 404:
            toast.error("کاربر یافت نشد", {
            description: "کاربری با این مشخصات وجود ندارد.",
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
