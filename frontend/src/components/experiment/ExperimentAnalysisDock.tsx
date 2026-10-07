'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { CaretDownIcon, CaretUpIcon, FlowArrowIcon, XIcon } from '@phosphor-icons/react';
import type { ExperimentProtocol, ExperimentRun } from '../../types/api';
import { VideoAnalysisProgress } from './VideoAnalysisProgress';

const preference = 'trial:experiment-analysis-hidden';
const stages = ['Methodology', 'Frames', 'Evidence', 'Verdicts'];

export function ExperimentAnalysisDock({
  job,
  protocol,
  onSkipDemo,
}: {
  job: ExperimentRun;
  protocol?: ExperimentProtocol;
  onSkipDemo?: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [hidden, setHidden] = useState(false);
  const panelId = useId();
  const toggle = useRef<HTMLButtonElement>(null);
  const restore = useRef<HTMLButtonElement>(null);
  const reduced = useReducedMotion();
  const active = ['queued', 'running'].includes(job.status);
  const complete = job.status === 'succeeded';
  const phase = complete
    ? 4
    : job.result.analysis_stage === 'verification'
      ? 3
      : job.result.analysis_stage === 'inspection'
        ? 2
        : job.result.analysis_stage === 'frames'
          ? 1
          : 0;
  const status = complete
    ? 'Complete'
    : job.status === 'failed'
      ? 'Needs attention'
      : job.status === 'queued'
        ? 'Queued'
        : ['Reading methodology', 'Sampling frames', 'Inspecting evidence', 'Checking verdicts'][
            phase
          ];
  const transition = { duration: reduced ? 0 : 0.24, ease: [0.32, 0.72, 0, 1] as const };

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sessionStorage exists only after mount; reading it here keeps server and client HTML equal
      setHidden(sessionStorage.getItem(preference) === 'true');
    } catch {
      /* Preferences are optional. */
    }
  }, []);
  useEffect(() => {
    if (!open) return;
    function onEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
        toggle.current?.focus({ preventScroll: true });
      }
    }
    window.addEventListener('keydown', onEscape);
    return () => window.removeEventListener('keydown', onEscape);
  }, [open]);
  function hide(value: boolean) {
    setHidden(value);
    setOpen(false);
    try {
      sessionStorage.setItem(preference, String(value));
    } catch {
      /* Preferences are optional. */
    }
    requestAnimationFrame(() =>
      value
        ? restore.current?.focus({ preventScroll: true })
        : toggle.current?.focus({ preventScroll: true }),
    );
  }

  if (hidden)
    return (
      <div className="analysis-restore">
        {onSkipDemo && (
          <button
            className="analysis-dock-skip"
            aria-label="Skip to demo results"
            title="Open saved results for the demo. Live analysis continues in the background."
            onClick={onSkipDemo}
          >
            Skip to the good bit <span aria-hidden>↗</span>
          </button>
        )}
        <button ref={restore} onClick={() => hide(false)}>
          <CaretUpIcon size={12} aria-hidden />
          Show analysis
        </button>
      </div>
    );

  return (
    <aside
      aria-label="Analysis details"
      className="analysis-dock"
      data-open={open}
      data-active={active}
      data-demo-skip={!!onSkipDemo}
    >
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="details"
            id={panelId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={transition}
            className="analysis-dock-reveal"
          >
            <div className="analysis-dock-content">
              <div className="analysis-dock-inner">
                <VideoAnalysisProgress job={job} protocol={protocol} />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="analysis-dock-bar">
        <div className="analysis-dock-inner analysis-dock-controls">
          <button
            ref={toggle}
            className="analysis-dock-toggle"
            aria-label={open ? 'Collapse analysis' : 'Expand analysis'}
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            onClick={() => setOpen(value => !value)}
          >
            <FlowArrowIcon size={17} className="analysis-dock-icon" aria-hidden />
            <span className="analysis-dock-title">How it worked</span>
            <span
              className={`analysis-dock-status ${complete ? 'text-pass' : job.status === 'failed' ? 'text-fail' : active ? 'text-running' : ''}`}
            >
              {active && <i aria-hidden />}
              {status}
            </span>
            <span className="analysis-dock-stages" aria-hidden>
              {stages.map((label, index) => (
                <span
                  key={label}
                  data-complete={index < phase}
                  data-current={active && index === phase}
                >
                  <small>{String(index + 1).padStart(2, '0')}</small>
                  {label}
                </span>
              ))}
            </span>
            <span className="analysis-dock-hint">{open ? 'Collapse' : 'Explore'}</span>
            {open ? <CaretDownIcon size={14} aria-hidden /> : <CaretUpIcon size={14} aria-hidden />}
          </button>
          {onSkipDemo && (
            <button
              className="analysis-dock-skip"
              aria-label="Skip to demo results"
              title="Open saved results for the demo. Live analysis continues in the background."
              onClick={onSkipDemo}
            >
              Skip to the good bit <span aria-hidden>↗</span>
            </button>
          )}
          <button
            className="analysis-dock-hide"
            aria-label="Hide analysis"
            title="Hide analysis for the demo"
            onClick={() => hide(true)}
          >
            <XIcon size={14} aria-hidden />
          </button>
        </div>
      </div>
    </aside>
  );
}
