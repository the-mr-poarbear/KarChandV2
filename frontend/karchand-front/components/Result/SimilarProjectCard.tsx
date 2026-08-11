import { SimilarProject } from "@/interfaces/similarProjects"
import { SquareArrowOutUpLeft } from "lucide-react"
import { Separator } from "../ui/separator"

interface Props{
    project:SimilarProject
}
const SimilarProjectCard = ({project}:Props) => {
  return (
    <div className="border-2 rounded-xl p-4 space-y-4">
        <div className="flex items-center w-full justify-between ">
            <div className="flex items-center gap-3">
                <p className="text-xl text-primary font-semibold">{project.title}</p>
                <SquareArrowOutUpLeft className="size-5 stroke-3 text-primary"/>
            </div>
            
            <p className="text-sm text-black/50 font-semibold">{project.date}</p>
        </div>

        <Separator/>

        <p>{project.description}</p>

        <Separator/>
        <div className="grid grid-cols-3 w-full ">
            <p className="font-semibold "><span className="text-primary ">زمان: </span>{project.timeline_days} روز</p>
            {/* <Separator orientation="vertical"/> */}
            <p className="font-semibold "><span className="text-primary ">قیمت واقعی: </span>{project.actual_price_tomans} تومان</p>
            {/* <Separator orientation="vertical"/> */}
            <p className="font-semibold "><span className="text-primary ">قیمت به امروز: </span>{project.converted_price_tomans} تومان</p>

        </div>
    </div>
  )
}

export default SimilarProjectCard