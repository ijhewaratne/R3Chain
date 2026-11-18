import { useRef, type KeyboardEvent, type ReactNode } from "react";

export interface TabDefinition {
  id: string;
  label: string;
  content: ReactNode;
}

interface WorkspaceTabsProps {
  ariaLabel: string;
  tabs: readonly TabDefinition[];
  activeTab: string;
  onChangeTab: (tabId: string) => void;
}

export function WorkspaceTabs({
  ariaLabel,
  tabs,
  activeTab,
  onChangeTab,
}: WorkspaceTabsProps) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const handleKeyDown = (index: number, e: KeyboardEvent<HTMLButtonElement>) => {
    let nextIndex = index;
    if (e.key === "ArrowRight") {
      nextIndex = (index + 1) % tabs.length;
    } else if (e.key === "ArrowLeft") {
      nextIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (e.key === "Home") {
      nextIndex = 0;
    } else if (e.key === "End") {
      nextIndex = tabs.length - 1;
    } else {
      return;
    }
    e.preventDefault();
    const target = tabs[nextIndex];
    onChangeTab(target.id);
    buttonRefs.current[nextIndex]?.focus();
  };

  return (
    <div className="workspace-tabs">
      <nav className="workspace-nav" aria-label={ariaLabel}>
        <div role="tablist" aria-label={ariaLabel}>
          {tabs.map((tab, idx) => {
            const selected = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                ref={(el) => {
                  buttonRefs.current[idx] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${tab.id}`}
                aria-selected={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => onChangeTab(tab.id)}
                onKeyDown={(e) => handleKeyDown(idx, e)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </nav>

      {tabs.map((tab) => {
        const selected = tab.id === activeTab;
        return (
          <div
            key={tab.id}
            role="tabpanel"
            id={`panel-${tab.id}`}
            aria-labelledby={`tab-${tab.id}`}
            hidden={!selected}
          >
            <div className="panel-surface">{tab.content}</div>
          </div>
        );
      })}
    </div>
  );
}
