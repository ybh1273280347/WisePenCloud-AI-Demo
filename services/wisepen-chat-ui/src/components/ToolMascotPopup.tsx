import { useEffect, useRef, useState } from "react";

type ToolMascotStatus = "running" | "completed" | "error";

type ToolMascotPopupProps = {
  status: ToolMascotStatus;
};

export function ToolMascotPopup({ status }: ToolMascotPopupProps) {
  const previousStatusRef = useRef<ToolMascotStatus | null>(null);
  const [visibleStatus, setVisibleStatus] = useState<"success" | "error" | null>(null);
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const previousStatus = previousStatusRef.current;
    previousStatusRef.current = status;

    if (status === "running") {
      setVisibleStatus(null);
      return;
    }

    if (previousStatus === status) {
      return;
    }

    const nextVisibleStatus = status === "completed" ? "success" : "error";
    setVisibleStatus(nextVisibleStatus);

    const timer = window.setTimeout(() => {
      setVisibleStatus(null);
    }, 2200);

    return () => window.clearTimeout(timer);
  }, [status]);

  if (!visibleStatus) {
    return null;
  }

  const src =
    visibleStatus === "success"
      ? "/mascot/tool-success.png"
      : "/mascot/tool-error.png";

  if (hiddenSources.has(src)) {
    return null;
  }

  return (
    <div className={`tool-mascot-popup tool-mascot-popup-${visibleStatus}`} aria-hidden="true">
      <div className="tool-mascot-frame">
        <img
          src={src}
          alt=""
          onError={() => {
            setHiddenSources((current) => new Set(current).add(src));
            setVisibleStatus(null);
          }}
        />
      </div>
    </div>
  );
}
