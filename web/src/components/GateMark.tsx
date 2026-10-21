import type { GateStatus } from "../content/about";
import { IconCheck, IconCross, IconNotApplicable } from "./icons";

const GATE: Record<GateStatus, { text: string; style: string; Icon: typeof IconCheck }> = {
  passes: { text: "Passes", style: "border-met-line bg-met-bg text-met-fg", Icon: IconCheck },
  fails: { text: "Fails", style: "border-notmet-line bg-notmet-bg text-notmet-fg", Icon: IconCross },
  reported: { text: "Reported", style: "border-na-line bg-na-bg text-na-fg", Icon: IconNotApplicable },
};

/** A release gate's status as an icon and a word, never a color alone. */
export function GateMark({ status }: { status: GateStatus }) {
  const { text, style, Icon } = GATE[status];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${style}`}>
      <Icon size={13} />
      {text}
    </span>
  );
}
