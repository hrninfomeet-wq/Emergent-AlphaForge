import { LAYOUTS, defaultLayout, gridTemplate, readLayout, saveLayout, LAYOUT_KEY } from './chartLayouts';
test('layout areas are rectangular, distinct and cover the requested counts', () => {
  expect(new Set(LAYOUTS.map(layout => layout.count))).toEqual(new Set([1, 2, 3, 4, 6]));
  for (const layout of LAYOUTS) {
    const width = layout.areas[0].length;
    expect(layout.areas.every(row => row.length === width)).toBe(true);
    expect([...new Set(layout.areas.flat())].sort((a, b) => a - b)).toEqual(Array.from({length: layout.count}, (_, i) => i));
    for (let i = 0; i < layout.count; i++) {
      const cells = [];
      layout.areas.forEach((row, y) => row.forEach((value, x) => { if (value === i) cells.push([x, y]); }));
      const xs = cells.map(c => c[0]), ys = cells.map(c => c[1]);
      expect(cells.length).toBe((Math.max(...xs) - Math.min(...xs) + 1) * (Math.max(...ys) - Math.min(...ys) + 1));
    }
    expect(gridTemplate(layout)).toContain('tile0');
  }
});
test('state round-trips independent tiles; corrupt or future versions are preserved and rejected', () => {
  const map = new Map(); const storage = { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value) };
  const state = defaultLayout(); state.layout = 'six-grid'; state.active = 5; state.tiles[4].timeframe = '1d';
  expect(saveLayout(storage, state)).toBe('');
  expect(readLayout(storage).state).toEqual(state);
  const prior = map.get(LAYOUT_KEY); expect(saveLayout(storage, { ...state, version: 99 })).toContain('invalid'); expect(map.get(LAYOUT_KEY)).toBe(prior);
  const bad = JSON.stringify({ ...state, version: 99 }); map.set(LAYOUT_KEY, bad);
  const result = readLayout(storage); expect(result.state.layout).toBe('one'); expect(result.writable).toBe(false); expect(map.get(LAYOUT_KEY)).toBe(bad);
});
