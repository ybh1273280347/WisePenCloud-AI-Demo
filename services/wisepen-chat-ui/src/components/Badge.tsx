type BadgeProps = {
  status: "running" | "completed" | "error" | "streaming" | "stopped";
};

export function Badge({ status }: BadgeProps) {
  const labels: Record<BadgeProps["status"], string> = {
    running: "运行中",
    completed: "成功",
    error: "失败",
    streaming: "生成中",
    stopped: "已停止",
  };
  const label = labels[status];
  return <span className={`badge badge-${status}`}>{label}</span>;
}
