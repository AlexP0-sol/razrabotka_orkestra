/* Vendored zero-CDN offline topological rank helper for ORCHESTRA Operator Console (DoD-24). */
window.OrchestraVendorGraph = {
  computeRanks(nodes) {
    const ranks = {};
    const byId = Object.fromEntries((nodes || []).map((n) => [n.id, n]));
    function visit(id, stack = new Set()) {
      if (ranks[id] !== undefined) return ranks[id];
      if (stack.has(id) || !byId[id]) return 0;
      stack.add(id);
      const deps = byId[id].dependencies || [];
      let best = 0;
      for (const dep of deps) {
        best = Math.max(best, visit(dep, stack) + 1);
      }
      stack.delete(id);
      ranks[id] = best;
      return best;
    }
    for (const id of Object.keys(byId)) visit(id);
    return ranks;
  },
};
