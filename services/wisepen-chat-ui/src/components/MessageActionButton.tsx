import type { ButtonHTMLAttributes } from "react";

type MessageActionIcon = "copy" | "edit" | "regenerate";

type MessageActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: MessageActionIcon;
  active?: boolean;
  feedback?: string;
};

export function MessageActionButton({
  icon,
  active = false,
  feedback,
  className,
  ...props
}: MessageActionButtonProps) {
  const classes = [
    "message-action-button",
    `message-action-${icon}`,
    active ? "message-action-active" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button type="button" className={classes} {...props}>
      <MessageActionSvg icon={icon} />
      {feedback ? (
        <span className="message-action-feedback" aria-live="polite">
          {feedback}
        </span>
      ) : null}
    </button>
  );
}

function MessageActionSvg({ icon }: { icon: MessageActionIcon }) {
  if (icon === "copy") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="8" y="7" width="11" height="11" rx="2.2" />
        <path d="M5 14.5V6.8C5 5.3 6.3 4 7.8 4h7.7" />
      </svg>
    );
  }

  if (icon === "edit") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4.8 19.2l4.1-1 9.4-9.4a2.1 2.1 0 0 0-3-3L5.9 15.2l-1.1 4Z" />
        <path d="M13.8 7.3l3 3" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 6v5h-5" />
      <path d="M4 18v-5h5" />
      <path d="M6.1 10a6.7 6.7 0 0 1 11-2.6L20 10" />
      <path d="M17.9 14a6.7 6.7 0 0 1-11 2.6L4 14" />
    </svg>
  );
}
