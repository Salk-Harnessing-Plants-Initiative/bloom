export interface TreeNode {
  name: string;
  branchLength: number;
  children: TreeNode[];
}

export function parseNewick(newick: string): TreeNode {
  const s = newick.trim().replace(/;$/, "");
  let pos = 0;

  function parseNode(): TreeNode {
    const node: TreeNode = { name: "", branchLength: 0, children: [] };
    if (s[pos] === "(") {
      pos++;
      node.children.push(parseNode());
      while (s[pos] === ",") { pos++; node.children.push(parseNode()); }
      pos++;
    }
    let label = "";
    while (pos < s.length && s[pos] !== ":" && s[pos] !== "," && s[pos] !== ")" && s[pos] !== "(") { label += s[pos]; pos++; }
    node.name = label;
    if (pos < s.length && s[pos] === ":") {
      pos++;
      let numStr = "";
      while (pos < s.length && s[pos] !== "," && s[pos] !== ")" && s[pos] !== "(") { numStr += s[pos]; pos++; }
      node.branchLength = parseFloat(numStr) || 0;
    }
    return node;
  }

  return parseNode();
}

export function getLeaves(node: TreeNode): TreeNode[] {
  if (node.children.length === 0) return [node];
  return node.children.flatMap(getLeaves);
}

export function getSpeciesFromLeaf(name: string): string {
  const sep = name.includes("|") ? "|" : ":";
  return name.split(sep)[0].toLowerCase();
}

export function findMRCA(node: TreeNode, leafNames: Set<string>): TreeNode | null {
  const leaves = getLeaves(node);
  const nodeLeafNames = new Set(leaves.map((l) => l.name));
  const containsAll = [...leafNames].every((name) => nodeLeafNames.has(name));
  if (!containsAll) return null;
  for (const child of node.children) {
    const childMRCA = findMRCA(child, leafNames);
    if (childMRCA) return childMRCA;
  }
  return node;
}

export function countTerminals(node: TreeNode): number {
  if (node.children.length === 0) return 1;
  return node.children.reduce((sum, child) => sum + countTerminals(child), 0);
}
