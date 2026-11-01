// A page opens to show a criterion's evidence, or, with `lead`, a passage that may answer one the record could not.
export type PagePanel = { kind: "page"; docId: string; sentence: string | null; supports: string | null;
                          lead?: { start: number; end: number } };
export type Panel = { kind: "draft" } | { kind: "edit" } | { kind: "sent" } | PagePanel;
