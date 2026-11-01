import type { KeyboardEvent } from "react";

/** Ctrl or Cmd with Enter submits a short form from its text box. */
export function submitOnModEnter(e: KeyboardEvent<HTMLTextAreaElement>) {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }
}
