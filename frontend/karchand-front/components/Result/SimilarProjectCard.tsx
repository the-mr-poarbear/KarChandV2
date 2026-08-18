import { SimilarProject } from "@/interfaces/similarProjects"
import { SquareArrowOutUpLeft } from "lucide-react"
import { Separator } from "../ui/separator"
import { formatPersianDate } from "@/lib/formatPersianDate"
import ReactMarkdown from "react-markdown"
import Link from "next/link"
import ReadMore from "../ReadMore/ReadMore"
import { formatPrice } from "@/lib/formatPrice"

interface Props {
    project: SimilarProject
}



function getDesc(desc: string) {
    const isHtml = /<\/?[a-z][\s\S]*>/i.test(desc)

    const className =
        "prose max-w-none min-w-0 whitespace-pre-wrap wrap-break-word " +
        "[&_code]:break-all " +
        "[&_pre]:bg-accent [&_pre]:p-3 [&_pre]:rounded-lg [&_pre]:my-3 " +
        "[&_pre]:max-w-full [&_pre]:overflow-x-auto"

    return isHtml ? (
        <div
            className={className}
            dangerouslySetInnerHTML={{
                __html: desc,
            }}
        />
    ) : (
        <div className={className}>
            <ReactMarkdown>{desc}</ReactMarkdown>
        </div>
    )
}

const SimilarProjectCard = ({ project }: Props) => {



    return (
        <div className="border-2 rounded-xl   p-4 space-y-4 ">
            <div className="grid grid-cols-4 items-center w-full gap-4">
                {/* 75% */}
                <div className="col-span-3 flex items-center gap-3 min-w-0">
                    <p className="text-lg text-primary font-semibold ">
                        {project.title}
                    </p>
                    <Link
                        target="_blank"
                        rel="noopener noreferrer"
                        href={project.project_link}>
                        <SquareArrowOutUpLeft className="size-5 shrink-0 stroke-3 text-primary" />
                    </Link>
                </div>

                {/* 25% */}
                <p className="text-sm text-black/50 font-semibold text-left">
                    {formatPersianDate(project.date)}
                </p>
            </div>

            <Separator />

            <ReadMore maxHeight={200}>
                {getDesc(project.description)}

            </ReadMore>


            <Separator />
            <div className="grid grid-cols-5 w-full text-sm">
                <p className="font-semibold "><span className="text-primary ">زمان: </span>
                 {project.timeline_days > 0 ?
                 project.timeline_days
                : "-"}

                  روز</p>
                
                {project.actual_price_tomans > 0 
                ?
                <>
                    <p className="font-semibold col-span-2"><span className="text-primary ">قیمت واقعی: </span>{formatPrice(project.actual_price_tomans)} <span className="text-xs text-black/60">تومان</span></p>
    
                    <p className="font-semibold col-span-2"><span className="text-primary ">قیمت به امروز: </span>{formatPrice(project.converted_price_tomans)} <span className="text-xs text-black/60">تومان</span></p>
                </>
                :
                <>
                    <div className="font-semibold col-span-2 flex gap-2"><p className="text-primary ">بازه بودجه: </p><div><p>{formatPrice(project.max_budget)}  <span className="text-xs text-black/60">تومان</span></p> <p>{formatPrice(project.min_budget)} <span className="text-xs text-black/60">تومان</span></p></div></div>
    
                    <div className="font-semibold col-span-2 flex gap-2"><p className="text-primary "> بودجه به امروز: </p><div><p>{formatPrice(project.converted_max_budget_tomans)}  <span className="text-xs text-black/60">تومان</span></p> <p>{formatPrice(project.converted_min_budget_tomans)} <span className="text-xs text-black/60">تومان</span></p></div></div>
                </>
                }

            </div>
        </div>
    )
}

export default SimilarProjectCard