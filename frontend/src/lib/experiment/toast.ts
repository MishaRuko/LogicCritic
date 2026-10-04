import { createKumoToastManager } from '@cloudflare/kumo';
export const toasts = createKumoToastManager();
export const notify = (title: string) => toasts.add({ title });
