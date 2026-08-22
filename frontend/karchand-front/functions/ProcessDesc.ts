import { APIURL } from "@/data/consts";
import { ProjectPrediction } from "@/interfaces/projectPrediction";
import { postFetch, updateFetch } from "@/lib/fetch";
import { toast } from "sonner";


const mock: ProjectPrediction = {"reasoning":"این پروژه یک پلتفرم درمانی آنلاین برای جستجو، مقایسه و رزرو نوبت پزشکان است که شامل پرداخت آنلاین، پنل کاربری بیماران و پزشکان، مدیریت نوبت‌ها و مشاوره آنلاین می‌باشد. با توجه به ویژگی‌های رزرو نوبت پرداخت‌دار بین ارائه‌دهندگان خدمت (پزشکان) و دریافت‌کنندگان (بیماران)، این پلتفرم ماهیت بازارگاه (Marketplace) و سامانه رزرو (Booking System) دارد. همچنین نیازمندی‌هایی مانند مشاوره آنلاین، اعلان‌های فوری و یادآوری نوبت به‌معنای نیاز به به‌روزرسانی‌های بلادرنگ است. جستجوی پیشرفته بر اساس تخصص و موقعیت مکانی، آپلود مدارک پزشکی و مدیریت سوابق نیز از ویژگی‌های بارز سیستم است که پیچیدگی متوسطی در منطق تجاری و رابط کاربری ایجاد می‌کند.","suggested_settings":{"features":["Authentication","User Management","User Profiles","Dashboard","Search","Filtering & Sorting","File Upload & Storage","Document Management","Booking & Reservation","Payment Processing","Order Management","Scheduling & Calendar","Messaging & Chat","Notifications","Map & Geolocation Integration","Comments & Reviews","Real-time Updates"],"boolean_modifiers":["real_time","web"],"application_types":["web_application","marketplace","booking_system","healthcare_system"],"technology_modifiers":["Custom Development"],"enum_modifiers":{"project_type":"new","ai_level":"none","business_logic":"medium","data_volume":"medium","integration_complexity":"medium","ui_complexity":"medium","algorithmic_complexity":"low"},"numeric_modifiers":{"external_api_count":3,"estimated_screens":15,"estimated_entities":8}},"estimated_price":38064631.07658206}

export async function ProcessDescML(prompt: string) : Promise<ProjectPrediction> {

    return mock;

    const result = await postFetch(
        APIURL + "extract_taxonomy_ml",
        JSON.stringify({
            "query": prompt
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
