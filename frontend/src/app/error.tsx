'use client';
import { Button } from '@cloudflare/kumo';
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-xl">The workspace could not be displayed</h1>
      <p className="text-sm text-zinc-500">
        Your graph is stored in the backend. Try loading it again.
      </p>
      <Button onClick={reset}>Retry workspace</Button>
    </main>
  );
}
