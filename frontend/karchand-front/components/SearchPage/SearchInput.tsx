"use client"

import TextareaAutosize from "react-textarea-autosize"

import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
} from "@/components/ui/input-group"
import { ReactNode, useRef, useState } from "react"
import { UseMutationResult } from "@tanstack/react-query"
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "../ui/select"

interface Props {
  mutationRag: UseMutationResult<any, Error, { projectDesc: string; mode:"rag"|"mixed" }, unknown>
  mutationML: UseMutationResult<any, Error, { projectDesc: string; }, unknown>
}

const searchMode = [
  { label: "مدل ماشین لرنینگ + شباهت تکسونومی", value: "ml" },
  { label: "RAG + شباهت برداری", value: "rag" },
  { label: "RAG + شباهت تکسونومی", value: "mixed" },
]

export function SearchInput({ mutationML, mutationRag }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [disable, setDisable] = useState(true)
  const [selectedSearchMode, setSelectedSearchMode] = useState<string>(searchMode[0].value)

  function handleClick() {
    if (selectedSearchMode == "ml") {
      mutationML.mutate({ projectDesc: textareaRef.current?.value ?? "" })
    }
    else {
      console.log(selectedSearchMode)
      mutationRag.mutate({ projectDesc: textareaRef.current?.value ?? "" , mode:selectedSearchMode=="mixed" ? "mixed" : "rag"  })
    }
  }

  return (
    <div className="grid w-full lg:w-2/3 gap-6 ">
      <InputGroup className="bg-white rounded-xl">
        <TextareaAutosize
          onChange={(e) => { e.target.value.length > 0 ? setDisable(false) : setDisable(true) }}
          ref={textareaRef}
          data-slot="input-group-control"
          className="flex field-sizing-content min-h-16 w-full resize-none rounded-md bg-transparent px-3 py-2.5 text-base transition-[color,box-shadow] outline-none md:text-sm"
          placeholder="پروژه ی خود را توضیح دهید"
        />
        <InputGroupAddon align="block-end">

          <Select defaultValue={searchMode[0].value} onValueChange={(e) => {console.log(e) ; e && setSelectedSearchMode(e)}} items={searchMode}>
            <SelectTrigger className="w-fit ms-auto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className='w-fit'>
              <SelectGroup className='w-fit'>
                <SelectLabel className=''>نوع سرچ</SelectLabel>
                {searchMode.map((item) => (
                  <SelectItem className='text-xs' key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>

          <InputGroupButton disabled={(!textareaRef.current || textareaRef.current?.value.length == 0)} className=" font-semibold" size="sm" variant="default"
            onClick={() => handleClick()}>
            فرستادن
          </InputGroupButton>

        </InputGroupAddon>
      </InputGroup>
    </div>
  )
}

export default SearchInput;