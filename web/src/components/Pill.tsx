import type { ComponentType } from "react";
import { PILL_TEXT, type PillState } from "../lib/pill-text";
import { IconAlert, IconBan, IconCheck, IconCross, IconNotApplicable, IconWarning, type IconProps } from "./icons";

const PILL_ICON: Record<PillState, ComponentType<IconProps>> = {
  met: IconCheck,
  not_met: IconCross,
  insufficient: IconWarning,
  invalidated: IconBan,
  false_approval: IconAlert,
  not_applicable: IconNotApplicable,
};

const PILL_STYLE: Record<PillState, string> = {
  met: "border-met-line bg-met-bg text-met-fg",
  not_met: "border-notmet-line bg-notmet-bg text-notmet-fg",
  insufficient: "border-insufficient-line bg-insufficient-bg text-insufficient-fg",
  invalidated: "border-invalidated-line bg-invalidated-bg text-invalidated-fg",
  // The only solid fill among the states: the one outcome that must not be missed.
  false_approval: "border-danger-deep bg-danger text-on-danger font-bold",
  not_applicable: "border-na-line bg-na-bg text-na-fg",
};

/**
 * A verdict state as an icon and words in a rounded pill. The words and the icon's shape say the
 * state; the colors only reinforce it.
 */
export function Pill({
  state,
  prefix,
  large = false,
}: {
  state: PillState;
  /** Words before the state, such as "Label:". */
  prefix?: string;
  large?: boolean;
}) {
  const Icon = PILL_ICON[state];
  return (
    <span
      data-outcome={state}
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border font-semibold ${
        large ? "px-3 py-1 text-base" : "px-2 py-0.5 text-xs"
      } ${PILL_STYLE[state]}`}
    >
      <Icon size={large ? 18 : 13} />
      <span>
        {prefix ? `${prefix} ` : ""}
        {PILL_TEXT[state]}
      </span>
    </span>
  );
}
