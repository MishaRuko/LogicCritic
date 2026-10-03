// Shared utility sets for the workspace's recurring text roles.
export const label = 'text-[10px] font-medium text-zinc-500';
export const sectionLabel = `${label} mt-[21px] mb-[7px] block`;
export const finePrint = 'my-2.5 text-[10px] leading-[1.75] text-subtle';
export const textLink = 'inline-flex items-center gap-1.5 text-[10px] text-zinc-600 underline underline-offset-3';
export const inlineError = 'mt-[13px] text-[11px] leading-[1.7] text-fail';
export const connected = 'mt-3 text-[10px] leading-[1.7] text-pass';
export const panel = 'h-full overflow-y-auto px-[max(24px,calc((100%-900px)/2))] py-8 [scrollbar-width:thin] max-[900px]:p-[25px] max-[700px]:p-5';
export const panelSection = 'mt-7 border-t border-line pt-[22px] [&>h2]:mb-2.5 [&>h2]:text-[13px] [&>h2]:font-medium';
export const dialog = 'max-h-[calc(100dvh-60px)] overflow-y-auto bg-[#fdfdfd] p-[25px] text-xs [scrollbar-width:thin] max-[700px]:p-5 [&_[data-slot=dialog-description]]:text-[11px] [&_[data-slot=dialog-description]]:leading-[1.75] [&_[data-slot=dialog-description]]:text-zinc-500';
export const dialogHeading = 'mb-3 flex items-center justify-between gap-5 [&_h2]:text-lg [&_h2]:font-medium [&_h2]:tracking-[-.035em]';
export const dialogActions = 'mt-[23px] flex items-center justify-between gap-5 border-t border-line pt-[18px] max-[700px]:gap-2.5 [&_button]:text-[11px]';
export const inspectorFrame = 'absolute top-3 right-3 bottom-3 z-9 flex w-[326px] flex-col overflow-hidden rounded-lg border border-line bg-[#fdfdfd] px-[18px] pt-3.5 max-[900px]:right-[9px] max-[900px]:w-[292px] max-[900px]:px-3.5 max-[700px]:top-2.5 max-[700px]:right-2.5 max-[700px]:bottom-[100px] max-[700px]:w-[min(326px,calc(100%-20px))] [&_[role=tab]]:text-[10px] [&_[role=tablist]]:mt-1.5';
export const dependency = 'h-auto! w-full justify-between! whitespace-normal! rounded-none! border-b border-[#f0f0f2] py-2! pl-0! text-left text-[10px]! font-normal! leading-normal! [&_svg]:shrink-0';
