import {useEffect, useMemo, useState} from "react";
import {ChatPage} from "./components/ChatPage";
import {MockNotePage} from "./components/MockNotePage";
import {
  WISEPEN_NAVIGATE_TO_NOTE_BLOCK,
  type ProvenanceNavigationEvent,
} from "./utils/provenanceNavigation";
import "./styles.css";

type RouteState =
  | {page: "chat"}
  | {page: "note"; resourceId: string; blockId?: string | null};

function parseRoute(): RouteState {
  const noteMatch = window.location.pathname.match(/^\/app\/note\/([^/?#]+)/);
  if (!noteMatch) {
    return {page: "chat"};
  }

  const params = new URLSearchParams(window.location.search);
  return {
    page: "note",
    resourceId: decodeURIComponent(noteMatch[1]),
    blockId: params.get("blockId"),
  };
}

export default function App() {
  const [route, setRoute] = useState<RouteState>(() => parseRoute());

  useEffect(() => {
    const syncRoute = () => setRoute(parseRoute());
    window.addEventListener("popstate", syncRoute);
    return () => window.removeEventListener("popstate", syncRoute);
  }, []);

  useEffect(() => {
    const handleNavigate = (event: Event) => {
      const detail = (event as ProvenanceNavigationEvent).detail;
      const target = detail?.target;
      if (!target?.resourceId || !target.blockId) {
        return;
      }

      const next = `/app/note/${encodeURIComponent(target.resourceId)}?blockId=${encodeURIComponent(target.blockId)}`;
      window.history.pushState({}, "", next);
      setRoute({
        page: "note",
        resourceId: target.resourceId,
        blockId: target.blockId,
      });
    };

    window.addEventListener(WISEPEN_NAVIGATE_TO_NOTE_BLOCK, handleNavigate);
    return () => window.removeEventListener(WISEPEN_NAVIGATE_TO_NOTE_BLOCK, handleNavigate);
  }, []);

  const openChat = useMemo(
    () => () => {
      window.history.pushState({}, "", "/");
      setRoute({page: "chat"});
    },
    [],
  );

  if (route.page === "note") {
    return (
      <MockNotePage
        resourceId={route.resourceId}
        blockId={route.blockId}
        onOpenChat={openChat}
      />
    );
  }

  return <ChatPage />;
}
