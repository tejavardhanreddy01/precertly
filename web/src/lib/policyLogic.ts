import type { Logic, PolicyVariant } from "./types";

/** One entry of a logic group: a criterion id, or a nested group. */
export type LogicItem = string | Logic;

/** "all_of" when the group has an all_of list, otherwise "any_of" (the same test the screen uses). */
export function kindOf(logic: Logic): "all_of" | "any_of" {
  return logic.all_of ? "all_of" : "any_of";
}

function itemsOf(logic: Logic): LogicItem[] {
  return (logic.all_of ?? logic.any_of ?? []).slice();
}

/**
 * The items of an all_of with every all_of nested directly inside it merged in. An all_of inside an
 * all_of is the same condition as its items listed in the parent, so this changes no meaning. An
 * any_of is never entered or merged: it stays one item, whole.
 */
function flattenAllOf(logic: Logic): LogicItem[] {
  return itemsOf(logic).flatMap((item) =>
    typeof item !== "string" && kindOf(item) === "all_of" ? flattenAllOf(item) : [item],
  );
}

/**
 * A group with its redundant wrappers removed: a group whose only child is a group of the same kind
 * is replaced by that child, and the same is done below it. Other groups are copied unchanged.
 */
export function collapseLogic(logic: Logic): Logic {
  const kind = kindOf(logic);
  const items = itemsOf(logic).map((item) => (typeof item === "string" ? item : collapseLogic(item)));
  const only = items.length === 1 ? items[0] : undefined;
  if (only !== undefined && typeof only !== "string" && kindOf(only) === kind) return only;
  return { [kind]: items };
}

/**
 * What a variant requires outright: the items of its top-level all_of, with nested all_ofs merged
 * in. Null when the top level is an any_of, because no single item in it is required on its own.
 */
export function requiredItems(variant: PolicyVariant): LogicItem[] | null {
  return kindOf(variant.logic) === "all_of" ? flattenAllOf(variant.logic) : null;
}

/**
 * The criterion ids every variant requires, as plain items of its top-level all_of (nested
 * all_ofs merged in), once each. An id that a variant reaches only through an any_of is not
 * required by that variant, so it is never counted, whatever the other variants do. A policy with
 * one variant has none. The order is that of `order` (the policy's criteria) when given, else the
 * order in the first variant.
 */
export function sharedCriteria(variants: readonly PolicyVariant[], order?: readonly string[]): string[] {
  if (variants.length < 2) return [];
  const lists = variants.map((v) => requiredItems(v));
  const ids = lists.map((items) => (items ? items.filter((i): i is string => typeof i === "string") : null));
  const [first, ...rest] = ids;
  if (!first || rest.some((r) => r === null)) return [];
  const common = first.filter((id, i) => first.indexOf(id) === i && rest.every((r) => r?.includes(id)));
  if (!order) return common;
  const rank = (id: string) => {
    const at = order.indexOf(id);
    return at === -1 ? order.length : at;
  };
  return [...common].sort((a, b) => rank(a) - rank(b)); // Array.sort is stable
}

/** A canonical text for a collapsed item, with the members of every group sorted, so order never matters. */
function canon(item: LogicItem): string {
  if (typeof item === "string") return JSON.stringify(item);
  const members = itemsOf(item).map(canon).sort();
  return `${kindOf(item)}(${members.join(",")})`;
}

/**
 * The any_of groups every variant requires as a top-level item (nested all_ofs merged in) and that
 * are equal in all of them: same members, in any order, compared recursively after redundant
 * wrappers are collapsed. A group that differs in one member in one variant is not shared. Nothing
 * is shared by a policy with one variant, or when any variant's top level is an any_of (it has no
 * required items). Each group comes once, in the first variant's order.
 */
export function sharedGroups(variants: readonly PolicyVariant[]): Logic[] {
  if (variants.length < 2) return [];
  const lists = variants.map((v) => requiredItems(v));
  const groupsOf = (items: LogicItem[]) =>
    items.filter((i): i is Logic => typeof i !== "string").map((g) => collapseLogic(g));
  const [first, ...rest] = lists;
  if (!first || rest.some((r) => r === null)) return [];
  const others = rest.map((r) => new Set(groupsOf(r ?? []).map(canon)));
  const seen = new Set<string>();
  return groupsOf(first).filter((g) => {
    const key = canon(g);
    if (seen.has(key) || !others.every((o) => o.has(key))) return false;
    seen.add(key);
    return true;
  });
}

/**
 * The variant's items that are not in `shared` (plain ids) or `groups` (see sharedGroups): plain
 * criterion ids and any groups (an any_of, or a group that cannot be merged), each group with
 * redundant wrappers collapsed. A variant whose top level is an any_of is returned as that one group.
 */
export function ownCriteria(variant: PolicyVariant, shared: readonly string[], groups: readonly Logic[] = []): LogicItem[] {
  const items = requiredItems(variant);
  if (items === null) return [collapseLogic(variant.logic)];
  const hoisted = new Set(groups.map((g) => canon(collapseLogic(g))));
  return items
    .map((item) => (typeof item === "string" ? item : collapseLogic(item)))
    .filter((item) => (typeof item === "string" ? !shared.includes(item) : !hoisted.has(canon(item))));
}

/**
 * True when the visible list of a variant (its own items, nothing hoisted) already shows the logic
 * exactly: a top-level all_of with no all_of directly inside it to merge and no wrapper to collapse.
 */
export function listShowsExactLogic(variant: PolicyVariant, hasShared: boolean): boolean {
  if (hasShared || kindOf(variant.logic) !== "all_of") return false;
  const nestedAllOf = itemsOf(variant.logic).some((i) => typeof i !== "string" && kindOf(i) === "all_of");
  return !nestedAllOf && JSON.stringify(collapseLogic(variant.logic)) === JSON.stringify(variant.logic);
}
