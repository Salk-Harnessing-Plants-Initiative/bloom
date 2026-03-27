interface NJNode {
  label: string | null;
  left: NJNode | null;
  right: NJNode | null;
  leftDist: number;
  rightDist: number;
}

export function neighborJoining(distMatrix: number[][], labels: string[]): string {
  const n = labels.length;
  if (n === 0) return ";";
  if (n === 1) return `${escapeLabel(labels[0])};`;
  if (n === 2) {
    const d = distMatrix[0][1];
    return `(${escapeLabel(labels[0])}:${fmt(d / 2)},${escapeLabel(labels[1])}:${fmt(d / 2)});`;
  }

  const d: number[][] = distMatrix.map((row) => [...row]);
  const nodes: NJNode[] = labels.map((label) => ({ label, left: null, right: null, leftDist: 0, rightDist: 0 }));
  const active = new Set<number>();
  for (let i = 0; i < n; i++) active.add(i);

  const maxNodes = 2 * n - 2;
  while (d.length < maxNodes) d.push(new Array(maxNodes).fill(0));
  for (const row of d) while (row.length < maxNodes) row.push(0);
  while (nodes.length < maxNodes) nodes.push({ label: null, left: null, right: null, leftDist: 0, rightDist: 0 });

  let nextNode = n;

  while (active.size > 2) {
    const activeArr = Array.from(active);
    const r = activeArr.length;
    const rowSum = new Map<number, number>();
    for (const i of activeArr) { let s = 0; for (const j of activeArr) s += d[i][j]; rowSum.set(i, s); }

    let minQ = Infinity, minI = -1, minJ = -1;
    for (let ai = 0; ai < activeArr.length; ai++) {
      const i = activeArr[ai];
      for (let aj = ai + 1; aj < activeArr.length; aj++) {
        const j = activeArr[aj];
        const q = (r - 2) * d[i][j] - rowSum.get(i)! - rowSum.get(j)!;
        if (q < minQ) { minQ = q; minI = i; minJ = j; }
      }
    }

    const dij = d[minI][minJ];
    const ri = rowSum.get(minI)!, rj = rowSum.get(minJ)!;
    const limbI = dij / 2 + (ri - rj) / (2 * (r - 2));
    const limbJ = dij - limbI;

    const u = nextNode++;
    nodes[u] = { label: null, left: nodes[minI], right: nodes[minJ], leftDist: Math.max(0, limbI), rightDist: Math.max(0, limbJ) };

    for (const k of activeArr) {
      if (k === minI || k === minJ) continue;
      d[u][k] = (d[minI][k] + d[minJ][k] - dij) / 2;
      d[k][u] = d[u][k];
    }

    active.delete(minI);
    active.delete(minJ);
    active.add(u);
  }

  const remaining = Array.from(active);
  const [a, b] = remaining;
  const finalDist = d[a][b];
  const root: NJNode = { label: null, left: nodes[a], right: nodes[b], leftDist: Math.max(0, finalDist / 2), rightDist: Math.max(0, finalDist / 2) };

  return toNewick(root) + ";";
}

function toNewick(node: NJNode): string {
  if (node.left === null && node.right === null) return escapeLabel(node.label ?? "");
  const left = node.left ? `${toNewick(node.left)}:${fmt(node.leftDist)}` : "";
  const right = node.right ? `${toNewick(node.right)}:${fmt(node.rightDist)}` : "";
  return `(${left},${right})`;
}

function escapeLabel(label: string): string { return label.replace(/:/g, "|"); }
function fmt(n: number): string { return n.toFixed(6); }
