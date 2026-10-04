import Link from 'next/link';
import { ArrowRightIcon, FlaskIcon, TreeStructureIcon } from '@phosphor-icons/react/dist/ssr';

const parts = [
  { href: '/research', icon: TreeStructureIcon, step: 'Before the bench', title: 'Check the argument', body: 'Turn papers and notes into an argument graph. See the exact excerpts behind each claim, review proposed reasoning, and find what still has to be established.' },
  { href: '/experiment', icon: FlaskIcon, step: 'At the bench', title: 'Check the experiment', body: 'Compare a recording of the experiment with its method. Each step is verified, contradicted, skipped or unverifiable, with timestamps you can jump to.' },
];

/** The site entry: either part works alone; a verified argument can be carried on into the experiment check. */
export function Landing() {
  return <main className="flex min-h-screen flex-col justify-center bg-paper px-12 py-16 text-ink max-[700px]:px-6">
    <div className="mx-auto w-full max-w-4xl">
      <p className="text-[10px] text-zinc-500">From argument to experiment</p>
      <h1 className="mt-2 text-[42px] leading-tight tracking-[-.045em] max-[700px]:text-[32px]">Does the conclusion hold,<br/>and was the work done?</h1>
      <p className="mt-4 max-w-xl text-[13px] leading-relaxed text-zinc-500">Start with either part. Once an argument is verified, you can carry its method straight into the experiment check.</p>
      <div className="mt-10 grid grid-cols-2 gap-4 max-[800px]:grid-cols-1">
        {parts.map(({ href, icon: Icon, step, title, body }) => <Link key={href} href={href} className="group flex flex-col gap-3 rounded-lg border border-line bg-white p-6 transition-colors hover:border-zinc-400">
          <span className="flex items-center gap-2 text-[10px] text-zinc-500"><Icon size={14}/>{step}</span>
          <strong className="text-[18px] font-medium tracking-[-.02em]">{title}</strong>
          <span className="text-[12px] leading-relaxed text-zinc-500">{body}</span>
          <span className="mt-auto flex items-center gap-1 pt-2 text-[12px] font-medium">Open<ArrowRightIcon size={13} className="transition-transform group-hover:translate-x-0.5"/></span>
        </Link>)}
      </div>
    </div>
  </main>;
}
