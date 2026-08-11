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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { TaxonomyConfig } from "@/lib/taxonomy-types";

interface NumericModifiersModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fields: TaxonomyConfig["numeric_modifiers"]["fields"]; // key -> description
  selected: Record<string, number>;
  onApply: (next: Record<string, number>) => void;
}

export function NumericModifiersModal({
  open,
  onOpenChange,
  fields,
  selected,
  onApply,
}: NumericModifiersModalProps) {
  const [draft, setDraft] = useState<Record<string, number>>(selected);

  useEffect(() => {
    if (open) setDraft(selected);
  }, [open, selected]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sky-500">Numeric Modifiers</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 py-2">
          {Object.entries(fields).map(([key, description]) => (
            <div key={key} className="flex flex-col gap-1">
              <Label htmlFor={key} className="text-sm capitalize">
                {key.replace(/_/g, " ")}
              </Label>
              <Input
                id={key}
                type="number"
                min={0}
                value={draft[key] ?? 0}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                }
              />
            </div>
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
