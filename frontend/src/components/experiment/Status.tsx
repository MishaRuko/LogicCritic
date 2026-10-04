import { CheckIcon, QuestionMarkIcon, SkipForwardIcon, WarningIcon } from '@phosphor-icons/react';
import type { CheckResult, Status } from '../../lib/experiment/types';
const label = (s: Status) => s.charAt(0).toUpperCase() + s.slice(1);
export function StatusIcon({ status, size = 13 }: { status?: Status; size?: number }) {
  if (!status) return <span className="status-icon pending" style={{ width: size, height: size }} aria-label="Pending"/>;
  const Icon = status === 'verified' ? CheckIcon : status === 'contradicted' ? WarningIcon : status === 'skipped' ? SkipForwardIcon : QuestionMarkIcon;
  return <Icon className={`status-icon ${status}`} size={size} weight="bold" aria-label={label(status)}/>;
}
export function StatusLabel({ status }: { status: Status }) { return <span className={`status-label ${status}`}><StatusIcon status={status}/>{label(status)}</span>; }
const checkStatus: Record<CheckResult['result'], Status> = { confirmed: 'verified', contradicted: 'contradicted', not_visible: 'unverifiable' };
export function CheckIconFor({ result }: { result: CheckResult['result'] }) { return <StatusIcon status={checkStatus[result]} size={12}/>; }
