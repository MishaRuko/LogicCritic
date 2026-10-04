import type { Metadata, Viewport } from 'next';
import '@xyflow/react/dist/style.css';
import './globals.css';
import './experiment.css';
import { Providers } from './providers';
export const metadata: Metadata = {
  title: 'Trial | Research argument critic',
  description: 'Review scientific arguments, inspect exact source excerpts, and verify proof obligations.',
  icons: { icon: '/favicon.svg' },
};
export const viewport: Viewport = { themeColor: '#f7f6f2' };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body><Providers>{children}</Providers></body></html>;
}
