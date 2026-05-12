// chrome.jsx — App-shell wrapper used inside artboards.
// Renders TopBar + tab strip + content area, sized to fit the artboard.

function CCApp({ tab, paused, accent, feature, onTab, children, hideTabs }) {
  const f = feature || window.CC_FEATURE;
  const tabs = [
    { id: 'overview',  label: 'Overview',     icon: 'features'  },
    { id: 'dag',       label: 'DAG',          icon: 'dag', count: 25 },
    { id: 'tasks',     label: 'Tasks',        icon: 'tasks', count: 25 },
    { id: 'handoffs',  label: 'Handoffs',     icon: 'handoff'  },
    { id: 'validate',  label: 'Validation',   icon: 'validate' },
    { id: 'telemetry', label: 'Telemetry',    icon: 'telemetry'},
    { id: 'audit',     label: 'Interventions',icon: 'audit', count: 5 },
  ];
  return (
    <div className="cc cc-app">
      <TopBar feature={f} paused={paused} accent={accent} />
      {!hideTabs && <Tabs items={tabs} active={tab} onChange={onTab} />}
      <div className="cc-app-body">{children}</div>
    </div>
  );
}

// Top-level features-list shell — different from per-feature shell.
function CCAppList({ children, accent }) {
  return (
    <div className="cc cc-app">
      <TopBar accent={accent} />
      <div className="cc-app-body">{children}</div>
    </div>
  );
}

const __CHROME_STYLE = `
.cc-app { width: 100%; height: 100%; display: flex; flex-direction: column; background: var(--bg); }
.cc-app-body { flex: 1; min-height: 0; overflow: hidden; position: relative; display: flex; }
`;
if (typeof document !== 'undefined' && !document.getElementById('cc-chrome-style')) {
  const s = document.createElement('style');
  s.id = 'cc-chrome-style'; s.textContent = __CHROME_STYLE;
  document.head.appendChild(s);
}

Object.assign(window, { CCApp, CCAppList });
