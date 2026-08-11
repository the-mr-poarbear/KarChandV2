"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { MultiSelectModal } from "./multi-select-modal";
import { SingleSelectModal } from "./single-select-modal";
import { EnumModifiersModal } from "./enum-modifiers-modal";
import type { ProjectSettingsState, TaxonomyConfig } from "@/interfaces/taxonomy";

type ModalKey =
  | "features"
  | "project_type"
  | "boolean_modifiers"
  | "application_types"
  | "enum_modifiers"
  | "technology_modifiers"
  | null;

interface SettingsSummaryProps {
  taxonomy: TaxonomyConfig;
  settings: ProjectSettingsState | null;
  onChange: (next: ProjectSettingsState) => void;
}

function SummaryTile({
  label,
  value,
  onClick,
}: {
  label: string;
  value: string;
  onClick: () => void;
}) {
  return (
    <Card
      onClick={onClick}
      className="cursor-pointer px-4 py-3 text-center transition-colors hover:bg-accent/40"
    >
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="font-medium capitalize">{value}</p>
    </Card>
  );
}

export function SettingsSummary({ taxonomy, settings, onChange }: SettingsSummaryProps) {
  const [openModal, setOpenModal] = useState<ModalKey>(null);

  const enumFieldCount = Object.keys(taxonomy.enum_modifiers.fields).length;

  return (
    <>
    {settings ? 
    <>
      <div className="grid grid-cols-3 gap-3">
        <SummaryTile
          label="Features"
          value={`${settings?.features.length} checked`}
          onClick={() => setOpenModal("features")}
        />
        <SummaryTile
          label="Project Type"
          value={settings?.enum_modifiers.project_type?.replace(/_/g, " ") ?? "—"}
          onClick={() => setOpenModal("project_type")}
        />
        <SummaryTile
          label="Boolean Modifier"
          value={`${settings?.boolean_modifiers.length} checked`}
          onClick={() => setOpenModal("boolean_modifiers")}
        />
        <SummaryTile
          label="App type"
          value={`${settings?.application_types.length} checked`}
          onClick={() => setOpenModal("application_types")}
        />
        <SummaryTile
          label="Enum Modifier"
          value={`${enumFieldCount} chose`}
          onClick={() => setOpenModal("enum_modifiers")}
        />
        <SummaryTile
          label="Technologies"
          value={`${settings?.technology_modifiers.length} chose`}
          onClick={() => setOpenModal("technology_modifiers")}
        />
      </div>

      <MultiSelectModal
        open={openModal === "features"}
        onOpenChange={(o) => setOpenModal(o ? "features" : null)}
        title="Features"
        options={taxonomy.features.values}
        selected={settings?.features}
        onApply={(next) => onChange({ ...settings, features: next })}
      />

      <MultiSelectModal
        open={openModal === "application_types"}
        onOpenChange={(o) => setOpenModal(o ? "application_types" : null)}
        title="Application Type"
        options={taxonomy.application_types.values}
        selected={settings?.application_types}
        onApply={(next) => onChange({ ...settings, application_types: next })}
      />

      <MultiSelectModal
        open={openModal === "technology_modifiers"}
        onOpenChange={(o) => setOpenModal(o ? "technology_modifiers" : null)}
        title="Technologies"
        options={taxonomy.technology_modifiers.values}
        selected={settings?.technology_modifiers}
        onApply={(next) => onChange({ ...settings, technology_modifiers: next })}
      />

      <MultiSelectModal
        open={openModal === "boolean_modifiers"}
        onOpenChange={(o) => setOpenModal(o ? "boolean_modifiers" : null)}
        title="Boolean Modifiers"
        options={Object.keys(taxonomy.boolean_modifiers.fields)}
        selected={settings?.boolean_modifiers}
        onApply={(next) => onChange({ ...settings, boolean_modifiers: next })}
      />

      <SingleSelectModal
        open={openModal === "project_type"}
        onOpenChange={(o) => setOpenModal(o ? "project_type" : null)}
        title="Project Type"
        options={taxonomy.enum_modifiers.fields.project_type.values}
        selected={settings?.enum_modifiers.project_type}
        onApply={(next) =>
          onChange({
            ...settings,
            enum_modifiers: { ...settings?.enum_modifiers, project_type: next },
          })
        }
      />

      <EnumModifiersModal
        open={openModal === "enum_modifiers"}
        onOpenChange={(o) => setOpenModal(o ? "enum_modifiers" : null)}
        fields={taxonomy.enum_modifiers.fields}
        selected={settings?.enum_modifiers}
        exclude={["project_type"]}
        onApply={(next) =>
          onChange({
            ...settings,
            enum_modifiers: { ...settings?.enum_modifiers, ...next },
          })
        }
      />
    </>
    : <h3>nth to show</h3> }
    </>
  );
}
