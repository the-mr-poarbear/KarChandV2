"use client";

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";

interface EnumModifiersModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fields: Record<string, { values: string[]; description: string }>;
  selected: Record<string, string> | undefined;
  onApply: (next: Record<string, string>) => void;
  /** field keys to skip, e.g. ["project_type"] if it has its own tile */
  exclude?: string[];
}

export function EnumModifiersModal({
  open,
  onOpenChange,
  fields,
  selected,
  onApply,
  exclude = [],
}: EnumModifiersModalProps) {
  const [draft, setDraft] = useState<Record<string, string> | undefined>(selected);

  useEffect(() => {
    if (open && selected) setDraft(selected);
  }, [open, selected]);

  const entries = Object.entries(fields).filter(([key]) => !exclude.includes(key));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <DialogTitle className="text-sky-500">Enum Modifiers</DialogTitle>
        </DialogHeader>

        <div className="space-y-5 py-2 max-h-100 overflow-y-auto ">
          {draft && entries.map(([key, field]) => (
            <div key={key}>
              <p className="mb-2 text-sm font-medium capitalize">
                {key.replace(/_/g, " ")}
              </p>
              <RadioGroup
                value={draft[key]}
                onValueChange={(v) => setDraft((prev) => ({ ...prev, [key]: v }))}
                className="flex flex-wrap gap-2"
              >
                {field.values.map((val) => (
                  <div
                    key={val}
                    className="flex items-center gap-2 rounded-full border px-3 py-1.5"
                  >
                    <RadioGroupItem value={val} id={`${key}-${val}`} />
                    <Label
                      htmlFor={`${key}-${val}`}
                      className="cursor-pointer text-sm capitalize"
                    >
                      {val.replace(/_/g, " ")}
                    </Label>
                  </div>
                ))}
              </RadioGroup>
            </div>
          ))}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if(draft){
                onApply(draft);
                onOpenChange(false);
              }
            }}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
