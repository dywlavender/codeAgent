import test from "node:test";
import assert from "node:assert/strict";
import { CARD, callChainLayout } from "../src/features/graph/call-chain-layout.js";

test("cross-service call spine proceeds left to right without overlapping cards", () => {
  const nodes = ["controller", "service", "client", "remote"].map((id) => ({ id, label: id }));
  const edges = nodes.slice(1).map((node, i) => ({ source: nodes[i].id, target: node.id }));
  const { positions } = callChainLayout(nodes, edges, "controller");
  for (let i = 1; i < nodes.length; i++) assert.ok(positions[nodes[i].id].x > positions[nodes[i - 1].id].x + CARD.width);
});

test("branches have separate rows and recursive connections terminate", () => {
  const nodes = ["a", "b", "c", "d"].map((id) => ({ id, label: id }));
  const edges = [{ source: "a", target: "b" }, { source: "a", target: "c" }, { source: "b", target: "d" }, { source: "d", target: "a" }];
  const layout = callChainLayout(nodes, edges, "a");
  assert.ok(Math.abs(layout.positions.b.y - layout.positions.c.y) > CARD.height);
  assert.equal(4, Object.keys(layout.positions).length);
  assert.deepEqual(layout, callChainLayout([...nodes].reverse(), edges, "a"));
});
