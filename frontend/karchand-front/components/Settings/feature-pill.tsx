"use client";

import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface FeaturePillProps {
  label: string;
  checked: boolean;
  onToggle: () => void;
}

export function FeaturePill({ label, checked, onToggle }: FeaturePillProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition-colors",
        "hover:bg-accent/50",
        checked
          ? "border-border bg-background"
          : "border-border/60 bg-background text-muted-foreground"
      )}
    >
      <span
        className={cn(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] border",
          checked
            ? "border-sky-400 bg-sky-400 text-white"
            : "border-muted-foreground/40 bg-transparent"
        )}
      >
        {checked && <Check className="h-3 w-3" strokeWidth={3} />}
      </span>
      <span className="truncate">{label}</span>
    </button>
  );
}
