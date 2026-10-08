import { Button } from '@cloudflare/kumo';
import { ArrowClockwiseIcon, ChatCircleIcon, TrashIcon } from '@phosphor-icons/react';
import type { Workspace } from '../../types/api';
import { ResearchStages } from './ResearchExperiments';

/** The top bar: where you are, the three workflow stages, and the workspace actions. */
export function ResearchHeader({
  title,
  stage,
  hasResearch,
  verified,
  onStage,
  busy,
  workspaceId,
  workspaces,
  onOpen,
  chatToggle,
  materialActive,
  hasState,
  onMaterial,
  onExport,
  onRefresh,
  onDelete,
}: {
  title?: string;
  stage: number;
  hasResearch: boolean;
  verified: boolean;
  onStage: (stage: number) => void;
  busy: boolean;
  workspaceId?: string;
  workspaces?: Workspace[];
  onOpen: (id: string) => void;
  /** The chat or agent-details toggle, when the current tab has one. */
  chatToggle?: { open: boolean; docked: boolean; onToggle: () => void };
  materialActive: boolean;
  hasState: boolean;
  onMaterial: () => void;
  onExport: () => void;
  onRefresh: () => void;
  onDelete: () => void;
}) {
  return (
    <header className="research-header min-h-[52px] shrink-0 border-b border-line bg-zinc-50">
      <div className="research-header-title flex min-w-0 items-center gap-3 text-[11px] max-[700px]:hidden">
        <span className="text-zinc-500 max-[700px]:hidden">Research</span>
        <span className="text-zinc-300 max-[700px]:hidden">/</span>
        <strong className="truncate font-medium">{title ?? 'Add research'}</strong>
      </div>
      <ResearchStages
        stage={stage}
        hasResearch={hasResearch}
        verified={verified}
        onChange={onStage}
      />
      <div className="research-header-actions flex min-w-0 flex-wrap items-center justify-end gap-2">
        {chatToggle && (
          <Button
            size="xs"
            variant="ghost"
            aria-label={`${chatToggle.open ? 'Hide' : 'Show'} ${chatToggle.docked ? 'chat sidebar' : 'agent details'}`}
            aria-expanded={chatToggle.open}
            aria-controls="research-chat"
            icon={<ChatCircleIcon size={14} />}
            onClick={chatToggle.onToggle}
          >
            <span className="research-header-chat-label max-[700px]:hidden">
              {chatToggle.docked
                ? chatToggle.open
                  ? 'Hide chat'
                  : 'Show chat'
                : chatToggle.open
                  ? 'Hide details'
                  : 'Agent details'}
            </span>
          </Button>
        )}
        {workspaceId && (
          <select
            disabled={busy}
            aria-label="Switch workspace"
            className="max-w-32 rounded border border-line bg-transparent p-1 text-[10px] min-[701px]:hidden"
            value={workspaceId}
            onChange={e => onOpen(e.target.value)}
          >
            {workspaces?.map(w => (
              <option key={w.id} value={w.id}>
                {w.title}
              </option>
            ))}
          </select>
        )}
        {hasState && (
          <>
            <Button
              size="xs"
              variant={materialActive ? 'outline' : 'ghost'}
              disabled={busy}
              aria-pressed={materialActive}
              onClick={onMaterial}
            >
              Material
            </Button>
            <Button size="xs" variant="ghost" disabled={busy} onClick={onExport}>
              Export
            </Button>
            <Button
              size="xs"
              variant="ghost"
              shape="square"
              disabled={busy}
              aria-label="Refresh workspace"
              icon={<ArrowClockwiseIcon size={14} />}
              onClick={onRefresh}
            />
            <Button
              size="xs"
              variant="ghost"
              shape="square"
              disabled={busy}
              aria-label="Delete workspace"
              icon={<TrashIcon size={14} />}
              onClick={onDelete}
            />
          </>
        )}
      </div>
    </header>
  );
}

/** The confirmation shown before a workspace and its graph are deleted. */
export function DeleteBanner({
  title,
  busy,
  onKeep,
  onConfirm,
}: {
  title: string;
  busy: boolean;
  onKeep: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-red-200 bg-red-50 px-5 py-3 text-[11px]">
      <p>Delete “{title}” and all of its graph data? This cannot be undone.</p>
      <div className="flex gap-2">
        <Button size="xs" disabled={busy} variant="ghost" onClick={onKeep}>
          Keep workspace
        </Button>
        <Button size="xs" disabled={busy} variant="destructive" onClick={onConfirm}>
          Delete permanently
        </Button>
      </div>
    </div>
  );
}
