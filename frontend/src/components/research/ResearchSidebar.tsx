import { Button, cn } from '@cloudflare/kumo';
import { PlusIcon, SidebarSimpleIcon } from '@phosphor-icons/react';
import { labSample } from '../../lib/research/demo';
import type { Statement, Workspace } from '../../types/api';

const row =
  'h-auto! min-h-8 w-full justify-start! px-2! py-2! text-left text-[11px]! font-normal! whitespace-normal!';
const createdAt = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});

/** The left navigation: new research, the demo sample, workspaces and their conclusions. */
export function ResearchSidebar({
  collapsed,
  onToggle,
  busy,
  workspaces,
  currentId,
  onOpen,
  onOpenSample,
  targets,
  target,
  onTarget,
  apiStatus,
}: {
  collapsed: boolean;
  onToggle: () => void;
  busy: boolean;
  workspaces?: Workspace[];
  currentId?: string;
  /** Open a workspace, or start a new one when no id is given. */
  onOpen: (id?: string) => void;
  onOpenSample: () => void;
  targets: Statement[];
  target?: string;
  onTarget: (id: string) => void;
  apiStatus: string;
}) {
  return (
    <aside
      aria-label="Research navigation"
      className={cn(
        'flex shrink-0 flex-col border-r border-line bg-zinc-100 p-2',
        collapsed ? 'w-[58px]' : 'w-[208px] max-[700px]:w-[58px]',
      )}
    >
      <div className="flex h-11 items-center justify-between px-1">
        <button
          disabled={busy}
          className={cn('text-[13px] font-semibold', collapsed && 'hidden', 'max-[700px]:hidden')}
          onClick={() => onOpen()}
        >
          Trial
        </button>
        <Button
          size="sm"
          variant="ghost"
          shape="square"
          aria-label="Toggle research navigation"
          icon={<SidebarSimpleIcon size={17} />}
          onClick={onToggle}
        />
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        className="my-3 text-[11px]!"
        icon={<PlusIcon size={14} />}
        aria-label="New research workspace"
        onClick={() => onOpen()}
      >
        <span className={cn(collapsed && 'hidden', 'max-[700px]:hidden')}>New research</span>
      </Button>
      {!collapsed && (
        <section
          aria-label="Demo sample"
          className="mb-3 border-b border-line px-2 py-3 max-[700px]:hidden"
        >
          <p className="mb-2 text-[10px] text-zinc-500">Demo sample</p>
          <button
            disabled={busy}
            className="w-full text-left text-[11px] leading-5 hover:text-zinc-500 disabled:opacity-50"
            onClick={onOpenSample}
          >
            <strong className="block font-medium">DJI_08 · 30 seconds</strong>
            <span className="text-[10px] text-zinc-500">Cell preparation · protocol + video</span>
          </button>
          <div className="mt-3 flex gap-3 text-[10px] whitespace-nowrap text-zinc-500">
            <a href={labSample.protocol} download className="underline">
              Download protocol
            </a>
            <a href={labSample.video} download className="underline">
              Download video
            </a>
          </div>
        </section>
      )}
      {!collapsed && (
        <div className="flex-1 overflow-y-auto max-[700px]:hidden">
          <p className="px-2 py-2 text-[10px] text-zinc-500">Research workspaces</p>
          {workspaces?.map(w => (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              key={w.id}
              title={w.title}
              className={cn(row, currentId === w.id && 'bg-zinc-200!')}
              onClick={() => onOpen(w.id)}
            >
              <span className="min-w-0">
                <span className="line-clamp-2">{w.title}</span>
                {/* Questions often start alike; the time tells runs of the same question apart. */}
                <span className="mt-0.5 block text-[10px] text-zinc-500">
                  {createdAt.format(new Date(w.created_at))}
                </span>
              </span>
            </Button>
          ))}
          {!!targets.length && (
            <>
              <p className="mt-4 px-2 py-2 text-[10px] text-zinc-500">Target conclusions</p>
              {targets.map(s => (
                <Button
                  size="sm"
                  variant="ghost"
                  key={s.id}
                  className={cn(row, target === s.id && 'bg-zinc-200!')}
                  onClick={() => onTarget(s.id)}
                >
                  {s.text}
                </Button>
              ))}
            </>
          )}
        </div>
      )}
      <div className="mt-auto border-t border-line pt-3">
        <p className="px-2 text-[10px] text-zinc-500">{apiStatus}</p>
        <a
          href="/docs"
          target="_blank"
          rel="noreferrer"
          className={cn(
            'mt-3 block px-2 text-[10px] text-zinc-500 underline',
            collapsed && 'hidden',
            'max-[700px]:hidden',
          )}
        >
          API documentation
        </a>
      </div>
    </aside>
  );
}
