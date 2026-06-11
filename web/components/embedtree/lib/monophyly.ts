/**
 * Family monophyly check on a TreeNode.
 *
 * A set of leaves S is "monophyletic" in a tree iff there is some
 * subtree (a single internal node or leaf) whose leaves are exactly S.
 * In other words, the family is contained in one clade with no other
 * leaves mixed in.
 *
 * Used by the embedtree UI to highlight whether the query gene's
 * family forms one clean clade in the KNN-derived gene tree (a
 * scientifically meaningful signal of orthology vs paralogy).
 */
import type { TreeNode } from "./types";
import { isLeaf } from "./types";

/**
 * Returns true iff `familyMembers` form a single monophyletic clade in
 * `tree`. Member names not present in the tree are treated as missing;
 * if no family members are present the result is false (empty set is
 * a degenerate edge case; the UI never calls this with empty input).
 *
 * Conventions:
 *   - Comparison is case-sensitive (matches the underlying gene_id casing).
 *   - Duplicate family-member entries are deduplicated.
 *   - A single-leaf tree containing the only family member counts as
 *     monophyletic (trivially).
 */
export function isFamilyMonophyletic(
  tree: TreeNode,
  familyMembers: string[],
): boolean {
  const family = new Set(familyMembers);
  if (family.size === 0) return false;

  // Walk the tree post-order, computing each node's set of family-member
  // leaf names and total leaf count. A node is the "monophyletic root"
  // for the family iff its family-set size equals the family size AND
  // its total leaf count equals the family size (no non-family leaves
  // inside).
  let foundClade = false;

  function visit(node: TreeNode): { familyCount: number; totalCount: number } {
    if (isLeaf(node)) {
      const isFamilyLeaf = node.name !== undefined && family.has(node.name);
      return { familyCount: isFamilyLeaf ? 1 : 0, totalCount: 1 };
    }
    let familyCount = 0;
    let totalCount = 0;
    for (const child of node.children ?? []) {
      const s = visit(child);
      familyCount += s.familyCount;
      totalCount += s.totalCount;
    }
    if (
      familyCount === family.size &&
      totalCount === family.size &&
      !foundClade
    ) {
      foundClade = true;
    }
    return { familyCount, totalCount };
  }

  const rootSummary = visit(tree);

  // Edge case: the family lives entirely on one leaf (trivially monophyletic).
  if (
    rootSummary.familyCount === family.size &&
    family.size === 1 &&
    rootSummary.totalCount === 1
  ) {
    return true;
  }

  return foundClade;
}
