// Ordering shared by the sidebar's "By Individual" and the Manage page:
// an optional numeric sort_order comes first (ascending); anything without
// one follows, alphabetically. Names break ties between equal numbers.
export function compareByOrder(a, b) {
  const aHas = a.sort_order !== null && a.sort_order !== undefined;
  const bHas = b.sort_order !== null && b.sort_order !== undefined;
  if (aHas && bHas && a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
  if (aHas !== bHas) return aHas ? -1 : 1;
  return a.name.localeCompare(b.name);
}

export function sortIndividuals(list) {
  return [...list].sort(compareByOrder);
}

// Splits individuals into the sidebar's sections: each non-empty group (in
// group order) with its members (in member order), then everyone ungrouped.
export function buildIndividualSections(individuals, groups) {
  const sections = [...groups]
    .sort(compareByOrder)
    .map((group) => ({
      group,
      members: sortIndividuals(individuals.filter((individual) => individual.group_id === group.id)),
    }))
    .filter((section) => section.members.length > 0);
  const known = new Set(groups.map((group) => group.id));
  const ungrouped = sortIndividuals(individuals.filter((individual) => !known.has(individual.group_id)));
  return { sections, ungrouped };
}

// "" / whitespace -> null (unset); otherwise an integer, or undefined if invalid.
export function parseOrder(text) {
  const trimmed = String(text ?? "").trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isInteger(value) ? value : undefined;
}
