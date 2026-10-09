import type { ExperimentProtocol } from '../../types/api';

/** The alternative procedures a protocol's source describes (two device types, two sample
 *  preparations), in order of first appearance. Empty for a single procedure. */
export function protocolVariants(protocol?: ExperimentProtocol): string[] {
  return [
    ...new Set(
      (protocol?.protocol.steps ?? []).map(step => step.variant).filter((v): v is string => !!v),
    ),
  ];
}

/** The steps a recording follows: those every procedure shares plus one procedure's (the first
 *  when none is named), as the server does. A protocol without procedures is returned as is. */
export function forVariant<T extends ExperimentProtocol | undefined>(
  protocol: T,
  variant?: string | null,
): T {
  const variants = protocolVariants(protocol);
  if (!protocol || !variants.length) return protocol;
  const chosen = variant && variants.includes(variant) ? variant : variants[0];
  return {
    ...protocol,
    protocol: {
      ...protocol.protocol,
      steps: protocol.protocol.steps.filter(step => !step.variant || step.variant === chosen),
    },
  };
}
