export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
export const DOCUMENT_TYPES = ["application/pdf", "image/png", "image/jpeg", "image/webp"];
export const DOCUMENT_KINDS: { value: string; label: string }[] = [
  { value: "contract", label: "Contract" },
  { value: "receipt", label: "Receipt" },
  { value: "acknowledgement", label: "Developer acknowledgement" },
  { value: "other", label: "Other" },
];

/** A file the browser may send, or the reason it may not. The server checks the real bytes again. */
export function checkDocument(file: { size: number; type: string }): string | null {
  if (file.size <= 0) return "That file is empty.";
  if (file.size > MAX_DOCUMENT_BYTES) return "A document can be at most 10 MB.";
  if (!DOCUMENT_TYPES.includes(file.type)) return "Only PDF, PNG, JPEG and WebP files can be attached.";
  return null;
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
