export function formatDuration(startedAt: number, completedAt?: number): string {
  const end = completedAt ?? Date.now();
  const ms = Math.max(0, end - startedAt);
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}
