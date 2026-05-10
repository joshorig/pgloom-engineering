import { useEffect, useMemo, useState } from "react";
import { useApi, type CCEvent, type FeatureRow } from "./api";
import { TopBar, Tabs } from "./components/primitives";
import { connectRealtime, type RealtimeConnectionState } from "./realtime";
import { ReplanDialog } from "./ReplanDialog";
import { CouncilView, CouncilsList } from "./routes/CouncilView";
import { DagView } from "./routes/DagView";
import { FeatureOverview } from "./routes/FeatureOverview";
import { FeaturesList } from "./routes/FeaturesList";
import { HandoffView, InterventionView, ProjectRegistry, RealtimePanel, RecoveryView, SlotOccupancyView, TokenEconomyView, ValidationView } from "./routes/SimpleTables";
import { TaskView } from "./routes/TaskView";
import { TelemetryView } from "./routes/TelemetryView";

export function App() {
  const [events, setEvents] = useState<CCEvent[]>([]);
  const [realtime, setRealtime] = useState<RealtimeConnectionState>({ state: "connecting", attempt: 0 });
  const route = parseRoute(window.location.pathname);
  const { data: feature } = useApi<FeatureRow>(route.featureId ? `/api/features/${route.featureId}` : null);

  useEffect(() => connectRealtime(
    (event) => setEvents((prev) => [event, ...prev].slice(0, 80)),
    setRealtime
  ), []);

  const view = useMemo(() => {
    if (!route.featureId) {
      if (route.kind === "projects") return <ProjectRegistry />;
      if (route.kind === "realtime") return <RealtimePanel events={events} realtime={realtime} />;
      if (route.kind === "telemetry" && route.child === "slots") return <SlotOccupancyView />;
      if (route.kind === "telemetry" && route.child === "tokens") return <TokenEconomyView />;
      return <FeaturesList />;
    }
    switch (route.child) {
      case "task": return route.taskId ? <TaskView featureId={route.featureId} taskId={route.taskId} /> : <FeatureOverview featureId={route.featureId} events={events} />;
      case "councils": return route.councilId ? <CouncilView featureId={route.featureId} councilId={route.councilId} /> : <CouncilsList featureId={route.featureId} />;
      case "dag": return <DagView featureId={route.featureId} />;
      case "handoffs": return <HandoffView featureId={route.featureId} />;
      case "validation": return <ValidationView featureId={route.featureId} />;
      case "telemetry": return <TelemetryView featureId={route.featureId} />;
      case "telemetry/tokens": return <TokenEconomyView featureId={route.featureId} />;
      case "telemetry/slots": return <SlotOccupancyView featureId={route.featureId} />;
      case "recovery": return <RecoveryView featureId={route.featureId} />;
      case "interventions": return <InterventionView featureId={route.featureId} />;
      default: return <FeatureOverview featureId={route.featureId} events={events} />;
    }
  }, [route.child, route.featureId, route.kind, events, realtime]);

  return (
    <div className="cc cc-app" data-theme="dark" data-density="comfortable" data-accent="emerald" data-type="inter" data-pulse="on">
      <TopBar featureId={route.featureId} paused={feature?.paused} />
      {route.featureId && <Tabs featureId={route.featureId} active={route.child.split("/")[0]} />}
      <div className="cc-app-body">{view}</div>
      {route.featureId && <ReplanDialog featureId={route.featureId} />}
    </div>
  );
}

function parseRoute(pathname: string): { kind: string; featureId?: string; child: string; taskId?: string; councilId?: string } {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "feature" && parts[1]) {
    if (parts[2] === "task") {
      return { kind: "feature", featureId: parts[1], child: "task", taskId: parts[3] };
    }
    if (parts[2] === "councils") {
      return { kind: "feature", featureId: parts[1], child: "councils", councilId: parts[3] };
    }
    return { kind: "feature", featureId: parts[1], child: parts.slice(2).join("/") };
  }
  return { kind: parts[0] || "features", child: parts.slice(1).join("/") };
}
