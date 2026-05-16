import { useState } from "react";

export function DailyMascot() {
  const [visible, setVisible] = useState(true);

  if (!visible) {
    return null;
  }

  return (
    <div className="daily-mascot" aria-hidden="true">
      <div className="daily-mascot-frame">
        <img src="/mascot/daily.png" alt="" onError={() => setVisible(false)} />
      </div>
    </div>
  );
}
