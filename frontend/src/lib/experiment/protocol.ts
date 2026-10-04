import { z } from 'zod';
import sourceMethod from './fixtures/method.json';
import type { MethodContract } from './types';
const requirementSchema = z.object({
  id: z.string().min(1), title: z.string().min(1), description: z.string(), order: z.number().int().positive(),
  action: z.string().optional(), source: z.string().optional(), target: z.string().optional(),
  duration: z.object({ expectedSeconds: z.number().nonnegative().optional(), minSeconds: z.number().nonnegative().optional(), maxSeconds: z.number().nonnegative().optional() }).optional(),
  after: z.array(z.string()).optional(), before: z.array(z.string()).optional(), requiredEvidence: z.array(z.string()).optional(), checks: z.array(z.string()).optional(), details: z.array(z.string()).optional(), definingAction: z.string().optional(), visualGroup: z.string().optional(), caveats: z.array(z.string()).optional(),
  criticality: z.enum(['informational', 'important', 'critical']), category: z.enum(['identity', 'timing', 'action', 'ordering'])
});
export const methodSchema = z.object({ schemaVersion: z.literal(1), title: z.string().min(1), version: z.string().min(1), source: z.string(), summary: z.string().optional(), requirements: z.array(requirementSchema).min(1).max(100) });
/** A deliberately scoped text compiler for the selected public protocol.
 * Unknown wording is rejected for review rather than assigned invented predicates.
 */
export function compileProtocolText(text: string): MethodContract {
  const normalize = (s: string) => s.replace(/^\s*\d+[.)]\s*/, '').replace(/[–−]/g, '-').trim().toLowerCase().replace(/\s+/g, ' ');
  const lines = text.split('\n').filter(line => line.trim());
  if (!lines.length) throw new Error('Methodology is empty.');
  const requirements = lines.map((line, index) => {
    const match = sourceMethod.requirements.find(r => normalize(r.description) === normalize(line));
    if (!match) throw new Error(`Line ${index + 1} is outside the supported LSV protocol. Supply an explicit JSON contract to define its predicates.`);
    return { ...structuredClone(match), order: index + 1 };
  });
  return compileMethod(JSON.stringify({ ...sourceMethod, requirements }));
}
export function compileMethod(text: string): MethodContract {
  if (!text.trim().startsWith('{')) return compileProtocolText(text);
  const method = methodSchema.parse(JSON.parse(text));
  const ids = new Set(method.requirements.map(r => r.id));
  if (ids.size !== method.requirements.length) throw new Error('Requirement identifiers must be unique.');
  if (new Set(method.requirements.map(r => r.order)).size !== method.requirements.length) throw new Error('Requirement orders must be unique.');
  for (const r of method.requirements) {
    if (!r.action && !r.source && !r.target && !r.duration && !r.requiredEvidence?.length && !r.checks?.length) throw new Error(`${r.id} has no verifiable predicate.`);
    if ((r.duration?.minSeconds ?? 0) > (r.duration?.maxSeconds ?? Infinity)) throw new Error(`${r.id} has an invalid duration range.`);
    for (const id of [...r.after ?? [], ...r.before ?? []]) if (!ids.has(id) || id === r.id) throw new Error(`Invalid ordering anchor ${id}.`);
  }
  return method;
}
