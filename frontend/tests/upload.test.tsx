import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ResearchProgress, ResearchUpload } from '../src/components/research/ResearchUpload';
import type { Snapshot } from '../src/types/api';

describe('research upload', () => {
  it('turns pasted notes into a text document and submits it to build an argument', async () => {
    const submit = vi.fn().mockResolvedValue(true);
    render(<ResearchUpload busy={false} submit={submit}/>);
    expect(screen.getByRole('button', { name: 'Build argument' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Research text' }), { target: { value: '  Research notes  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Build argument' }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    const [files] = submit.mock.calls[0];
    expect(files[0].name).toBe('research-notes.txt');
    expect(files[0].type).toBe('text/plain');
    expect(files[0].size).toBe('Research notes'.length);
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''));
  });

  it('validates every file before submitting a batch', () => {
    const submit = vi.fn();
    render(<ResearchUpload busy={false} submit={submit}/>);
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [new File(['valid'], 'paper.md'), new File(['invalid'], 'image.png')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Build argument' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a PDF, Markdown or text file.');
    expect(submit).not.toHaveBeenCalled();
  });

  it('preserves selected material when submitting fails', async () => {
    const submit = vi.fn().mockResolvedValue(false);
    render(<ResearchUpload busy={false} submit={submit}/>);
    const paper = new File(['Paper content'], 'paper.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [paper] } });
    fireEvent.click(screen.getByRole('button', { name: 'Build argument' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith([paper]));
    expect(screen.getByText('paper.pdf')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Remove paper.pdf' }));
    expect(screen.getByRole('button', { name: 'Build argument' })).toBeDisabled();
  });

  it('rejects empty and oversized documents before submitting', () => {
    const submit = vi.fn();
    render(<ResearchUpload busy={false} submit={submit}/>);
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [new File([], 'empty.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Build argument' }));
    expect(screen.getByRole('alert')).toHaveTextContent('empty.txt is empty');
    const large = new File(['content'], 'large.pdf');
    Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [large] } });
    fireEvent.click(screen.getByRole('button', { name: 'Build argument' }));
    expect(screen.getByRole('alert')).toHaveTextContent('limit is 10 MB');
    expect(submit).not.toHaveBeenCalled();
  });

  it('keeps extraction in progress as claims appear, and completes only when the job finishes', () => {
    const state = { graph: { statements: [] }, jobs: [{ id: 'job', status: 'running', total_chunks: 3, completed_chunks: 0, created_at: '2026-10-03', error: null }] } as unknown as Snapshot;
    const progress = { stage: 'queued' as const, percent: 100, filename: 'paper.pdf', current: 1, total: 1 };
    const { rerender } = render(<ResearchProgress progress={progress} state={state} busy={false} onCancel={() => {}}/>);
    expect(screen.getByText('Extracting claims and evidence')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    const building = { ...state, graph: { ...state.graph, statements: [{}] }, jobs: [{ ...state.jobs[0], completed_chunks: 1 }] } as Snapshot;
    rerender(<ResearchProgress progress={progress} state={building} busy={false} onCancel={() => {}}/>);
    expect(screen.getByText('Building the argument graph')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '33');
    expect(screen.queryByText('Argument graph ready')).not.toBeInTheDocument();
    rerender(<ResearchProgress progress={progress} state={{ ...building, jobs: [{ ...building.jobs[0], status: 'succeeded', completed_chunks: 3 }] }} busy={false} onCancel={() => {}}/>);
    expect(screen.getByText('Argument graph ready')).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });
});
