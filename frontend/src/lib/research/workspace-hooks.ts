import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import * as api from './api';
import { chainHighlight } from './graph';
import type { VerificationTrace } from './verification';
import type { GraphQuestion, Snapshot, Verification } from '../../types/api';

/** The argument canvas's filters. `reset` clears the search and filters, not the evidence view. */
export function useGraphFilters() {
  const [query, setQuery] = useState('');
  const [lifecycle, setLifecycle] = useState('');
  const [source, setSource] = useState('');
  const [detailedGraph, setDetailedGraph] = useState(false);
  const [showWithdrawn, setShowWithdrawn] = useState(false);
  const reset = useCallback(() => {
    setQuery('');
    setLifecycle('');
    setSource('');
  }, []);
  return {
    query,
    setQuery,
    lifecycle,
    setLifecycle,
    source,
    setSource,
    detailedGraph,
    setDetailedGraph,
    showWithdrawn,
    setShowWithdrawn,
    reset,
  };
}

/** Questions asked of a workspace's graph. Each answer highlights the claims it rests on. */
export function useGraphQuestions(
  onHighlight: (ids: string[], question: string) => void,
  setBusy: (busy: boolean) => void,
) {
  const client = useQueryClient();
  const [questions, setQuestions] = useState<GraphQuestion[]>([]);
  async function ask(workspaceId: string, question: string) {
    const entry: GraphQuestion = {
      id: crypto.randomUUID(),
      workspaceId,
      question,
      createdAt: new Date().toISOString(),
      status: 'pending',
    };
    const history = questions
      .filter(q => q.workspaceId === workspaceId && q.answer)
      .slice(-6)
      .map(q => ({ question: q.question, answer: q.answer!.answer }));
    setQuestions(previous => [...previous, entry]);
    setBusy(true);
    try {
      const answer = await api.askGraph(workspaceId, question, history);
      setQuestions(previous =>
        previous.map(q => (q.id === entry.id ? { ...q, status: 'answered', answer } : q)),
      );
      const snapshot = client.getQueryData<Snapshot>(['snapshot', workspaceId]);
      const ids = snapshot
        ? chainHighlight(snapshot, answer.statement_ids, answer.step_ids)
        : [...answer.statement_ids, ...answer.step_ids];
      if (ids.length) onHighlight(ids, question);
    } catch (e) {
      setQuestions(previous =>
        previous.map(q =>
          q.id === entry.id
            ? { ...q, status: 'failed', error: e instanceof Error ? e.message : String(e) }
            : q,
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  return { questions, ask };
}

/** A streamed verification run: its live trace, and the latest result for each workspace.
 *  A run in progress is abandoned when the open workspace changes. */
export function useVerificationRun(workspaceId?: string) {
  const [trace, setTrace] = useState<VerificationTrace>();
  const [results, setResults] = useState<Record<string, Verification>>({});
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), [workspaceId]);

  async function run(workspace: string): Promise<Verification | undefined> {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setTrace({ workspaceId: workspace, status: 'running', events: [] });
    try {
      const result = await api.verifyLive(
        workspace,
        async event => {
          setTrace(previous =>
            previous?.workspaceId === workspace
              ? { ...previous, events: [...previous.events, event] }
              : previous,
          );
          // Give each real rule event time to be read and its graph transition to be seen.
          if (
            event.type === 'rule_started' &&
            !window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
          )
            await new Promise(resolve => window.setTimeout(resolve, 500));
        },
        current.signal,
      );
      setResults(previous => ({ ...previous, [workspace]: result }));
      setTrace(previous =>
        previous?.workspaceId === workspace ? { ...previous, status: 'complete' } : previous,
      );
      return result;
    } catch (error) {
      if (current.signal.aborted) return undefined;
      setTrace(previous =>
        previous?.workspaceId === workspace ? { ...previous, status: 'failed' } : previous,
      );
      throw error;
    }
  }
  return { trace, results, run };
}
