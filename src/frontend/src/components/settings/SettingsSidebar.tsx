import { useState } from 'react';
import { SettingsTab, SettingsGroup } from '../../types/settings';

interface SettingsSidebarProps {
  tabs: SettingsTab[];
  groups: SettingsGroup[];
  selectedTab: string | null;
  onSelectTab: (tabName: string) => void;
  mode: 'sidebar' | 'list';
}

// Map icon names to SVG paths
const getIcon = (iconName?: string) => {
  switch (iconName) {
    case 'settings':
    case 'cog':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      );
    case 'folder':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
        </svg>
      );
    case 'shield':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
        </svg>
      );
    case 'globe':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418" />
        </svg>
      );
    case 'download':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
        </svg>
      );
    case 'book':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
        </svg>
      );
    case 'library':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 21v-8.25M15.75 21v-8.25M8.25 21v-8.25M3 9l9-6 9 6m-1.5 12V10.332A48.36 48.36 0 0012 9.75c-2.551 0-5.056.2-7.5.582V21M3 21h18M12 6.75h.008v.008H12V6.75z" />
        </svg>
      );
    case 'beaker':
    case 'wrench':
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
        </svg>
      );
    default:
      return (
        <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
        </svg>
      );
  }
};

// Chevron icon for expandable groups
const ChevronIcon = ({ expanded }: { expanded: boolean }) => (
  <svg
    className={`w-5 h-5 opacity-30 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
    stroke="currentColor"
  >
    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
  </svg>
);

// Represents either a tab, a group, or a section header in the sorted list
type SidebarItem =
  | { type: 'tab'; tab: SettingsTab; order: number }
  | { type: 'group'; group: SettingsGroup; tabs: SettingsTab[]; order: number }
  | { type: 'section'; label: string; order: number };

// Section headers for organizing the sidebar
// Can trigger before a group (beforeGroup) or before a tab (beforeTab)
const SECTION_HEADERS: { beforeGroup?: string; beforeTab?: string; label: string }[] = [
  { beforeGroup: 'direct_download', label: 'Release Sources' },
  { beforeTab: 'hardcover', label: 'Metadata Providers' },
];

export const SettingsSidebar = ({
  tabs,
  groups,
  selectedTab,
  onSelectTab,
  mode,
}: SettingsSidebarProps) => {
  // Track which groups are expanded (all closed by default)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set()
  );

  const toggleGroup = (groupName: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  };

  // Build grouped tabs map
  const groupedTabs = new Map<string, SettingsTab[]>();
  tabs.forEach((tab) => {
    if (tab.group) {
      const existing = groupedTabs.get(tab.group) || [];
      existing.push(tab);
      groupedTabs.set(tab.group, existing);
    }
  });

  // Build a unified sorted list of tabs and groups
  const sidebarItems: SidebarItem[] = [];

  // Add ungrouped tabs (with section headers where needed)
  tabs.forEach((tab) => {
    if (!tab.group) {
      // Check if this tab needs a section header before it
      const sectionHeader = SECTION_HEADERS.find((s) => s.beforeTab === tab.name);
      if (sectionHeader) {
        sidebarItems.push({ type: 'section', label: sectionHeader.label, order: tab.order - 0.5 });
      }
      sidebarItems.push({ type: 'tab', tab, order: tab.order });
    }
  });

  // Add groups (with their tabs) and section headers
  groups.forEach((group) => {
    const groupTabList = groupedTabs.get(group.name) || [];
    if (groupTabList.length > 0) {
      // Check if this group needs a section header before it
      const sectionHeader = SECTION_HEADERS.find((s) => s.beforeGroup === group.name);
      if (sectionHeader) {
        // Insert section header just before this group (order - 0.5 to sort before)
        sidebarItems.push({ type: 'section', label: sectionHeader.label, order: group.order - 0.5 });
      }
      sidebarItems.push({ type: 'group', group, tabs: groupTabList, order: group.order });
    }
  });

  // Sort by order
  sidebarItems.sort((a, b) => a.order - b.order);

  if (mode === 'list') {
    // Mobile: Clean list style with inset dividers
    return (
      <div
        className="flex-1 overflow-y-auto"
        style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
      >
        {sidebarItems.map((item, itemIndex) => {
          if (item.type === 'section') {
            return (
              <div key={item.label} className="px-5 pt-6 pb-2">
                <span className="text-xs font-semibold uppercase tracking-wider opacity-50">
                  {item.label}
                </span>
              </div>
            );
          }

          if (item.type === 'tab') {
            return (
              <div key={item.tab.name}>
                <button
                  onClick={() => onSelectTab(item.tab.name)}
                  className="w-full flex items-center gap-4 px-5 py-4
                             active:bg-[var(--hover-surface)] transition-colors text-left"
                >
                  <span className="opacity-50">{getIcon(item.tab.icon)}</span>
                  <span className="flex-1">{item.tab.displayName}</span>
                </button>
                {itemIndex < sidebarItems.length - 1 && (
                  <div className="ml-14 mr-5 border-b border-[var(--border-muted)]" />
                )}
              </div>
            );
          }

          // Group
          const isExpanded = expandedGroups.has(item.group.name);
          return (
            <div key={item.group.name}>
              <button
                onClick={() => toggleGroup(item.group.name)}
                className="w-full flex items-center gap-4 px-5 py-4
                           active:bg-[var(--hover-surface)] transition-colors text-left"
              >
                <span className="opacity-50">{getIcon(item.group.icon)}</span>
                <span className="flex-1">{item.group.displayName}</span>
                <ChevronIcon expanded={isExpanded} />
              </button>

              {isExpanded && (
                <div className="bg-[var(--bg-soft)]/50">
                  {item.tabs.map((tab, index) => (
                    <div key={tab.name}>
                      <button
                        onClick={() => onSelectTab(tab.name)}
                        className="w-full flex items-center gap-4 pl-14 pr-5 py-3.5
                                   active:bg-[var(--hover-surface)] transition-colors text-left"
                      >
                        <span className="flex-1 text-[15px]">{tab.displayName}</span>
                      </button>
                      {index < item.tabs.length - 1 && (
                        <div className="ml-14 mr-5 border-b border-[var(--border-muted)]" />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {itemIndex < sidebarItems.length - 1 && (
                <div className="ml-14 mr-5 border-b border-[var(--border-muted)]" />
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // Desktop: Sidebar navigation
  return (
    <nav className="w-56 border-r border-[var(--border-muted)] py-2 flex-shrink-0 overflow-y-auto">
      {sidebarItems.map((item) => {
        if (item.type === 'section') {
          return (
            <div key={item.label} className="px-4 pt-5 pb-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider opacity-40">
                {item.label}
              </span>
            </div>
          );
        }

        if (item.type === 'tab') {
          return (
            <button
              key={item.tab.name}
              onClick={() => onSelectTab(item.tab.name)}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left
                          transition-colors ${
                            selectedTab === item.tab.name
                              ? 'bg-[var(--hover-action)] font-medium'
                              : 'hover:bg-[var(--hover-surface)]'
                          }`}
            >
              <span className="opacity-60">{getIcon(item.tab.icon)}</span>
              <span>{item.tab.displayName}</span>
            </button>
          );
        }

        // Group
        const isExpanded = expandedGroups.has(item.group.name);
        const hasSelectedTab = item.tabs.some((tab) => tab.name === selectedTab);

        return (
          <div key={item.group.name}>
            <button
              onClick={() => toggleGroup(item.group.name)}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left
                          transition-colors hover:bg-[var(--hover-surface)]
                          ${hasSelectedTab && !isExpanded ? 'bg-[var(--hover-action)]/50' : ''}`}
            >
              <span className="opacity-60">{getIcon(item.group.icon)}</span>
              <span className="flex-1">{item.group.displayName}</span>
              <ChevronIcon expanded={isExpanded} />
            </button>

            {isExpanded && (
              <div className="ml-8 border-l border-[var(--border-muted)]">
                {item.tabs.map((tab) => (
                  <button
                    key={tab.name}
                    onClick={() => onSelectTab(tab.name)}
                    className={`w-full flex items-center pl-4 pr-4 py-2 text-sm text-left
                                transition-colors ${
                                  selectedTab === tab.name
                                    ? 'bg-[var(--hover-action)] font-medium'
                                    : 'hover:bg-[var(--hover-surface)]'
                                }`}
                  >
                    <span>{tab.displayName}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
};
