export const CARD = { width: 264, height: 156 };

export function callChainLayout(nodes, edges, rootId) {
  const levels = new Map([[rootId, 0]]);
  const outgoing = new Map();
  for (const edge of edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  const queue = [rootId];
  for (let cursor = 0; cursor < queue.length; cursor++) {
    for (const target of outgoing.get(queue[cursor]) || []) {
      if (levels.has(target)) continue;
      levels.set(target, levels.get(queue[cursor]) + 1);
      queue.push(target);
    }
  }
  const columns = new Map();
  for (const node of nodes) {
    const column = node.column ?? levels.get(node.id) ?? 0;
    if (!columns.has(column)) columns.set(column, []);
    columns.get(column).push(node);
  }
  const rows = Math.max(1, ...[...columns.values()].map((items) => items.length));
  const height = Math.max(430, rows * (CARD.height + 64) + 100);
  const positions = {};
  const leftmost = Math.min(0, ...columns.keys());
  for (const [column, items] of columns) {
    items.sort((a, b) => a.label.localeCompare(b.label));
    items.forEach((node, row) => {
      positions[node.id] = { x: 48 + (column - leftmost) * (CARD.width + 92),
        y: height / 2 - (items.length * (CARD.height + 64) - 64) / 2 + row * (CARD.height + 64) };
    });
  }
  return { positions, width: Math.max(600, 96 + (Math.max(0, ...columns.keys()) - leftmost + 1) * (CARD.width + 92) - 92), height };
}

export function edgeCurve(a, b) {
  const x1 = a.x + CARD.width, y1 = a.y + CARD.height / 2;
  const x2 = b.x, y2 = b.y + CARD.height / 2;
  return x2 > x1
    ? `M ${x1} ${y1} C ${x1 + 46} ${y1}, ${x2 - 46} ${y2}, ${x2} ${y2}`
    : `M ${x1} ${y1} C ${x1 + 80} ${y1 - 160}, ${x2 - 80} ${y2 - 160}, ${x2} ${y2}`;
}
