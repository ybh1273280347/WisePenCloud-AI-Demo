export function formatJson(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }

  return JSON.stringify(value, null, 2);
}

export function asOutputText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value == null) {
    return "";
  }
  return formatJson(value);
}
