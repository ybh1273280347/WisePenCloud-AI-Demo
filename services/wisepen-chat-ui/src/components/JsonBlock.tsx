import { formatJson } from "../utils/safeJson";

type JsonBlockProps = {
  value: unknown;
  label: string;
  defaultOpen?: boolean;
  error?: boolean;
};

export function JsonBlock({ value, label, defaultOpen = false, error = false }: JsonBlockProps) {
  if (value == null || value === "") {
    return null;
  }

  return (
    <details className={`json-block ${error ? "json-block-error" : ""}`} open={defaultOpen}>
      <summary>{label}</summary>
      <pre>{typeof value === "string" ? value : formatJson(value)}</pre>
    </details>
  );
}
