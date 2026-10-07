import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ResearchProgress,
  ResearchComposer,
  type ResearchComposerMode,
} from '../src/components/research/ResearchUpload';
import { useState } from 'react';
import type { Snapshot } from '../src/types/api';

describe('research upload', () => {
  it('preserves pasted text and PDFs when switching modes and allows long material', async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    function Composer() {
      const [mode, setMode] = useState<ResearchComposerMode>('material');
      return <ResearchComposer mode={mode} onModeChange={setMode} busy={false} submit={submit} />;
    }
    render(<Composer />);
    const text = 'Research paragraph. '.repeat(150);
    const paper = new File(['Paper content'], 'paper.pdf', { type: 'application/pdf' });
    expect(screen.getByRole('button', { name: 'Material mode' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.queryByText('Research options')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Research message'), { target: { value: text } });
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [paper] } });
    fireEvent.click(screen.getByRole('button', { name: 'Agent mode' }));
    expect(screen.getByLabelText('Research message')).toHaveValue(text);
    expect(screen.getByText('paper.pdf')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Switch to Add material');
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Material mode' }));
    fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'material', prompt: text.trim(), files: [paper] }),
      ),
    );
  });

  it('uses Enter to send, preserves Shift+Enter for new lines, and ignores IME composition', async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />);
    const box = screen.getByLabelText('Research message');
    fireEvent.change(box, { target: { value: 'Research this question.' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true });
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  });

  it('keeps the draft after a provider error and when the first message creates its workspace', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('Research agent is unavailable.'));
    const { rerender } = render(
      <ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />,
    );
    fireEvent.change(screen.getByLabelText('Research message'), {
      target: { value: 'Does treatment X work?' },
    });
    rerender(
      <ResearchComposer
        mode="agent"
        onModeChange={() => {}}
        workspaceId="new-workspace"
        busy={false}
        submit={submit}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Research agent is unavailable.');
    expect(screen.getByLabelText('Research message')).toHaveValue('Does treatment X work?');
    rerender(
      <ResearchComposer
        mode="agent"
        onModeChange={() => {}}
        workspaceId="different-workspace"
        busy={false}
        submit={submit}
      />,
    );
    expect(screen.getByLabelText('Research message')).toHaveValue('');
  });
  it('sends a research prompt without turning the question into a source document', async () => {
    const submit = vi.fn().mockResolvedValue(true);
    render(<ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />);
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Research message' }), {
      target: { value: '  Research notes  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: 'Research notes',
        files: [],
        options: expect.objectContaining({ mode: 'guarded' }),
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Research message' })).toHaveValue(''),
    );
  });

  it('validates every file before submitting a batch', () => {
    const submit = vi.fn();
    render(<ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />);
    fireEvent.change(screen.getByLabelText('Research documents'), {
      target: { files: [new File(['valid'], 'paper.md'), new File(['invalid'], 'image.png')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a PDF, Markdown or text file.');
    expect(submit).not.toHaveBeenCalled();
  });

  it('preserves selected material when submitting fails', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('Agent unavailable'));
    render(<ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />);
    const paper = new File(['Paper content'], 'paper.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [paper] } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ files: [paper] })),
    );
    expect(screen.getByText('paper.pdf')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Remove paper.pdf' }));
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  });

  it('rejects empty and oversized documents before submitting', () => {
    const submit = vi.fn();
    render(<ResearchComposer mode="agent" onModeChange={() => {}} busy={false} submit={submit} />);
    fireEvent.change(screen.getByLabelText('Research documents'), {
      target: { files: [new File([], 'empty.txt')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByRole('alert')).toHaveTextContent('empty.txt is empty');
    const large = new File(['content'], 'large.pdf');
    Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [large] } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByRole('alert')).toHaveTextContent('limit is 10 MB');
    expect(submit).not.toHaveBeenCalled();
  });

  it('keeps extraction in progress as claims appear, and completes only when the job finishes', () => {
    const state = {
      graph: { statements: [] },
      jobs: [
        {
          id: 'job',
          status: 'running',
          total_chunks: 3,
          completed_chunks: 0,
          created_at: '2026-10-03',
          error: null,
        },
      ],
    } as unknown as Snapshot;
    const progress = {
      stage: 'queued' as const,
      percent: 100,
      filename: 'paper.pdf',
      current: 1,
      total: 1,
    };
    const { rerender } = render(
      <ResearchProgress progress={progress} state={state} busy={false} onCancel={() => {}} />,
    );
    expect(screen.getByText('Extracting claims and evidence')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    const building = {
      ...state,
      graph: { ...state.graph, statements: [{}] },
      jobs: [{ ...state.jobs[0], completed_chunks: 1 }],
    } as Snapshot;
    rerender(
      <ResearchProgress progress={progress} state={building} busy={false} onCancel={() => {}} />,
    );
    expect(screen.getByText('Building the argument graph')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '33');
    expect(screen.queryByText('Argument graph ready')).not.toBeInTheDocument();
    rerender(
      <ResearchProgress
        progress={progress}
        state={{
          ...building,
          jobs: [{ ...building.jobs[0], status: 'succeeded', completed_chunks: 3 }],
        }}
        busy={false}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Argument graph ready')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });
});
