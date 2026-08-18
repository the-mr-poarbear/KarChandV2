"use client"

import { useState } from "react"

interface ReadMoreProps {
    children: React.ReactNode
    maxHeight?: number
}

const ReadMore = ({
    children,
    maxHeight = 200,
}: ReadMoreProps) => {
    const [expanded, setExpanded] = useState(false)

    return (
        <div>
            <div
                className="overflow-hidden text-sm transition-[max-height] duration-300 ease-in-out "
                style={{
                    maxHeight: expanded ? "2000px" : `${maxHeight}px`,
                }}
            >
                {children}
            </div>

            <button
                type="button"
                onClick={() => setExpanded((prev) => !prev)}
                className="mt-2 text-sm font-semibold text-primary hover:underline"
            >
                {expanded ? "بستن" : "توضیحات بیشتر"}
            </button>
        </div>
    )
}

export default ReadMore