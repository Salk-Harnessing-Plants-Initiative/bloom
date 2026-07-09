/**
 * Shared tree types for embedtree algorithms.
 *
 * A TreeNode is either a leaf (has `name`, no `children`) or an internal
 * node (has `children`, may or may not have `name`). `distance` is the
 * branch length FROM THE PARENT; the root has `distance = undefined`.
 *
 * Used by neighborJoining (output), newick (I/O), and monophyly (input).
 * Kept arity-free (children: TreeNode[]) so the same type covers binary
 * trees produced by NJ and the variable-arity trees produced by parsing
 * external Newick strings.
 */
export interface TreeNode {
  /** Leaf label. Undefined on internal nodes (Newick supports labels on
   *  internal nodes too, but NJ doesn't produce them). */
  name?: string;
  /** Children. Undefined or empty array signals a leaf. */
  children?: TreeNode[];
  /** Branch length from the parent. Undefined at the root. */
  distance?: number;
}

/** Convenience predicate: a node is a leaf if it has no non-empty children. */
export function isLeaf(node: TreeNode): boolean {
  return !node.children || node.children.length === 0;
}
