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

interface SingleSelectModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  options: string[];
  selected: string | undefined;
  onApply: (next: string) => void;
}

export function SingleSelectModal({
  open,
  onOpenChange,
  title,
  options,
  selected,
  onApply,
}: SingleSelectModalProps) {
  const [draft, setDraft] = useState(selected);

  useEffect(() => {
    if (open) setDraft(selected);
  }, [open, selected]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sky-500">{title}</DialogTitle>
        </DialogHeader>

        <RadioGroup value={draft} onValueChange={setDraft} className="gap-2 py-2">
          {options.map((opt) => (
            <div
              key={opt}
              className="flex items-center gap-2 rounded-lg border px-3 py-2"
            >
              <RadioGroupItem value={opt} id={`opt-${opt}`} />
              <Label htmlFor={`opt-${opt}`} className="flex-1 cursor-pointer capitalize">
                {opt.replace(/_/g, " ")}
              </Label>
            </div>
          ))}
        </RadioGroup>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onApply(draft ?? "");
              onOpenChange(false);
            }}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
