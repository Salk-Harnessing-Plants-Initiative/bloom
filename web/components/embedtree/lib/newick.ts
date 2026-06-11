/**
 * Newick tree format I/O.
 *
 * Newick is the canonical plain-text representation of phylogenetic trees.
 * A leaf is just a name (e.g. `A`). An internal node is `(a,b,c)` where
 * each child can itself be a leaf or another parenthesized group. A
 * branch length attaches to a node as `:0.123` after the name or group.
 * The whole tree terminates with `;`.
 *
 * Examples:
 *   `A;`                           single leaf
 *   `(A:0.1,B:0.2);`               two leaves under a root
 *   `((A:0.1,B:0.2):0.05,C:0.3);`  nested
 *
 * Both directions are total inverses on trees produced by
 * `neighborJoining()`: `parseNewick(toNewick(t))` returns a tree
 * topologically identical to `t` with the same branch lengths
 * (to floating-point precision). The implementation does not
 * support edge labels, internal-node confidence values, or comments —
 * those are not produced by neighborJoining and not needed by the
 * UI's tree renderer.
 */
import type { TreeNode } from "./types";
import { isLeaf } from "./types";

// ─── serialize ───────────────────────────────────────────────────────────────

/**
 * Convert a TreeNode to a Newick string terminated by `;`.
 *
 * Branch lengths are rendered with `toString()` — JavaScript's default
 * which trims trailing zeros while preserving precision.
 */
export function toNewick(root: TreeNode): string {
  return `${nodeToNewick(root, true)};`;
}

function nodeToNewick(node: TreeNode, isRoot: boolean): string {
  let body: string;
  if (isLeaf(node)) {
    body = escapeName(node.name ?? "");
  } else {
    const children = node.children ?? [];
    const inner = children.map((c) => nodeToNewick(c, false)).join(",");
    body = `(${inner})`;
    if (node.name) {
      body += escapeName(node.name);
    }
  }
  if (!isRoot && node.distance !== undefined) {
    body += `:${node.distance}`;
  }
  return body;
}

/**
 * Escape a name for Newick output. Names containing whitespace or any
 * of the Newick-reserved characters get wrapped in single quotes (with
 * embedded single quotes doubled, per the spec).
 */
function escapeName(name: string): string {
  if (name === "") return "";
  if (/[\s(),:;'\[\]]/.test(name)) {
    return `'${name.replace(/'/g, "''")}'`;
  }
  return name;
}

// ─── parse ───────────────────────────────────────────────────────────────────

/**
 * Parse a Newick string into a TreeNode. Whitespace inside the string is
 * tolerated. The trailing `;` is optional.
 *
 * @throws Error on syntactically malformed input (unbalanced parens,
 *   trailing garbage, etc.).
 */
export function parseNewick(input: string): TreeNode {
  const cleaned = input.trim();
  if (cleaned.length === 0) {
    throw new Error("parseNewick: empty input");
  }
  // Strip trailing ';' if present so we don't confuse the parser.
  const body = cleaned.endsWith(";") ? cleaned.slice(0, -1) : cleaned;

  const parser = new Parser(body);
  const tree = parser.parseNode();
  parser.expectEnd();
  return tree;
}

class Parser {
  private pos = 0;

  constructor(private readonly src: string) {}

  parseNode(): TreeNode {
    this.skipSpace();
    let node: TreeNode;
    if (this.peek() === "(") {
      this.pos += 1; // consume '('
      const children: TreeNode[] = [];
      children.push(this.parseNode());
      this.skipSpace();
      while (this.peek() === ",") {
        this.pos += 1; // consume ','
        children.push(this.parseNode());
        this.skipSpace();
      }
      if (this.peek() !== ")") {
        throw new Error(
          `parseNewick: expected ')' at position ${this.pos}, got ${JSON.stringify(this.peek() ?? "<EOF>")}`,
        );
      }
      this.pos += 1; // consume ')'
      const internalName = this.readName();
      node = internalName
        ? { name: internalName, children }
        : { children };
    } else {
      // Leaf: just a name (possibly empty for an unlabeled leaf).
      const name = this.readName();
      node = name === "" ? { name: "" } : { name };
    }

    this.skipSpace();
    if (this.peek() === ":") {
      this.pos += 1;
      node.distance = this.readNumber();
      this.skipSpace();
    }
    return node;
  }

  expectEnd(): void {
    this.skipSpace();
    if (this.pos < this.src.length) {
      throw new Error(
        `parseNewick: trailing content at position ${this.pos}: ${JSON.stringify(
          this.src.slice(this.pos),
        )}`,
      );
    }
  }

  private peek(): string | undefined {
    return this.src[this.pos];
  }

  private skipSpace(): void {
    while (this.pos < this.src.length && /\s/.test(this.src[this.pos])) {
      this.pos += 1;
    }
  }

  private readName(): string {
    this.skipSpace();
    if (this.peek() === "'") {
      // Quoted name; doubled single-quote escapes one inside.
      this.pos += 1;
      let out = "";
      while (this.pos < this.src.length) {
        const ch = this.src[this.pos];
        if (ch === "'") {
          if (this.src[this.pos + 1] === "'") {
            out += "'";
            this.pos += 2;
          } else {
            this.pos += 1;
            return out;
          }
        } else {
          out += ch;
          this.pos += 1;
        }
      }
      throw new Error("parseNewick: unterminated quoted name");
    }
    let out = "";
    while (this.pos < this.src.length) {
      const ch = this.src[this.pos];
      if (/[(),:;]/.test(ch)) break;
      out += ch;
      this.pos += 1;
    }
    return out.trim();
  }

  private readNumber(): number {
    this.skipSpace();
    const start = this.pos;
    while (
      this.pos < this.src.length &&
      /[0-9eE+\-.]/.test(this.src[this.pos])
    ) {
      this.pos += 1;
    }
    const raw = this.src.slice(start, this.pos);
    const n = Number(raw);
    if (raw === "" || Number.isNaN(n)) {
      throw new Error(
        `parseNewick: invalid branch length at position ${start}: ${JSON.stringify(raw)}`,
      );
    }
    return n;
  }
}
