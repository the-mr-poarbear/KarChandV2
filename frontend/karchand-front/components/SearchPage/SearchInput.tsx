"use client"

import TextareaAutosize from "react-textarea-autosize"

import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
} from "@/components/ui/input-group"
import { ReactNode, useRef, useState } from "react"
import { UseMutationResult } from "@tanstack/react-query"

interface Props{
    mutation: UseMutationResult<any, Error, {projectDesc: string;}, unknown>
}

export function SearchInput({mutation}:Props) {
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const [disable , setDisable] = useState(true)
  return (
    <div  className="grid w-full sm:w-2/3 gap-6 ">
      <InputGroup className="bg-white rounded-xl">
        <TextareaAutosize
            onChange={(e)=>{e.target.value.length > 0 ? setDisable(false) : setDisable(true)}}
            ref={textareaRef}
            data-slot="input-group-control"
            className="flex field-sizing-content min-h-16 w-full resize-none rounded-md bg-transparent px-3 py-2.5 text-base transition-[color,box-shadow] outline-none md:text-sm"
            placeholder="پروژه ی خود را توضیح دهید"
        />
        <InputGroupAddon  align="block-end">
          <InputGroupButton disabled={(textareaRef.current?.value.length == 0)} className="ms-auto font-semibold" size="sm" variant="default" onClick={()=>mutation.mutate({projectDesc:textareaRef.current?.value??""})}>
            فرستادن
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    </div>
  )
}

export default SearchInput;