export type ProvenanceNoteTarget = {
  resourceKind?: string;
  resourceId: string;
  resourceVersion?: string | null;
  blockId: string;
  rowIndex?: number;
  columnName?: string;
  chartElementId?: string;
};

export const WISEPEN_NAVIGATE_TO_NOTE_BLOCK = "wisepen:navigate-to-note-block";

export type ProvenanceNavigationEvent = CustomEvent<{
  target: ProvenanceNoteTarget;
  source: "chart_preview" | string;
}>;

export function navigateToProvenanceNoteBlock(target: ProvenanceNoteTarget): void {
  window.dispatchEvent(
    new CustomEvent(WISEPEN_NAVIGATE_TO_NOTE_BLOCK, {
      detail: {
        target,
        source: "chart_preview",
      },
    }),
  );
}
