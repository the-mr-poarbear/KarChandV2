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
import { FeaturePill } from "./feature-pill";

interface MultiSelectModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  options: string[];
  selected: string[];
  onApply: (next: string[]) => void;
}

export function MultiSelectModal({
  open,
  onOpenChange,
  title,
  options,
  selected,
  onApply,
}: MultiSelectModalProps) {
  const [draft, setDraft] = useState<string[]>(selected);

  useEffect(() => {
    if (open) setDraft(selected);
  }, [open, selected]);

  function toggle(value: string) {
    setDraft((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-sky-500">{title}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-wrap gap-2 py-2">
          {options.map((opt) => (
            <FeaturePill
              key={opt}
              label={opt}
              checked={draft.includes(opt)}
              onToggle={() => toggle(opt)}
            />
          ))}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onApply(draft);
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
