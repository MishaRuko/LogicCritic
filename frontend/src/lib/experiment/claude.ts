import Anthropic from '@anthropic-ai/sdk';
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod';
import { AGENT_SYSTEM, AGENT_TOOLS, EXTRACTION_SYSTEM, MODEL, extractedMethodSchema, toContract } from './agent';
import { compileMethod } from './protocol';
import type { MethodContract } from './types';

/** Server-side only (Worker and Node scripts). The API key never reaches the browser. */
const FALLBACK = { betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' as const };

export type MethodDocument = { name: string } & ({ kind: 'pdf'; data: string } | { kind: 'text'; text: string });

export async function extractMethod(client: Anthropic, doc: MethodDocument): Promise<MethodContract> {
  const response = await client.beta.messages.parse({
    ...FALLBACK, model: MODEL, max_tokens: 16000,
    output_config: { effort: 'high', format: betaZodOutputFormat(extractedMethodSchema) },
    system: EXTRACTION_SYSTEM,
    messages: [{
      role: 'user',
      content: [
        doc.kind === 'pdf'
          ? { type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: doc.data }, title: doc.name }
          : { type: 'document', source: { type: 'text', media_type: 'text/plain', data: doc.text }, title: doc.name },
        { type: 'text', text: 'Extract the bench methodology from this document as verifiable steps.' }
      ]
    }]
  });
  if (response.stop_reason === 'refusal') throw new Error('The model declined to read this document.');
  if (!response.parsed_output) throw new Error('The model did not return a methodology.');
  if (!response.parsed_output.steps.length) throw new Error('No bench procedure was found in this document.');
  return compileMethod(JSON.stringify(toContract(response.parsed_output, doc.name)));
}

export async function agentTurn(client: Anthropic, messages: Anthropic.Beta.Messages.BetaMessageParam[]) {
  const message = await client.beta.messages.stream({
    ...FALLBACK, model: MODEL, max_tokens: 32000,
    thinking: { type: 'adaptive', display: 'summarized' }, output_config: { effort: 'high' },
    cache_control: { type: 'ephemeral' },
    system: AGENT_SYSTEM, tools: AGENT_TOOLS, messages
  }).finalMessage();
  return { content: message.content, stop_reason: message.stop_reason, usage: message.usage };
}
