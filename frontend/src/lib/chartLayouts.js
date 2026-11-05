import { INDEX_SHORTCUTS, TIMEFRAMES } from '../components/charts/chartPresentation';
import { defaultIndicators, saveIndicatorSettings } from './chartIndicatorConfig';
import { defaultAnalysisSettings, normalizeAnalysisSettings } from './chartAnalysisSettings';

export const LAYOUTS = [
  { id: 'one', label: 'Single', areas: [[0]] },
  { id: 'two-columns', label: 'Columns', areas: [[0, 1]] },
  { id: 'two-rows', label: 'Rows', areas: [[0], [1]] },
  { id: 'three-columns', label: 'Columns', areas: [[0, 1, 2]] },
  { id: 'three-rows', label: 'Rows', areas: [[0], [1], [2]] },
  { id: 'three-left', label: 'Large left', areas: [[0, 1], [0, 2]] },
  { id: 'three-top', label: 'Large top', areas: [[0, 0], [1, 2]] },
  { id: 'four-grid', label: 'Grid', areas: [[0, 1], [2, 3]] },
  { id: 'four-columns', label: 'Columns', areas: [[0, 1, 2, 3]] },
  { id: 'four-rows', label: 'Rows', areas: [[0], [1], [2], [3]] },
  { id: 'four-left', label: 'Large left', areas: [[0, 1], [0, 2], [0, 3]] },
  { id: 'four-top', label: 'Large top', areas: [[0, 0, 0], [1, 2, 3]] },
  { id: 'six-grid', label: 'Grid', areas: [[0, 1, 2], [3, 4, 5]] },
  { id: 'six-columns', label: 'Columns', areas: [[0, 1, 2, 3, 4, 5]] },
  { id: 'six-rows', label: 'Rows', areas: [[0], [1], [2], [3], [4], [5]] },
  { id: 'six-left', label: 'Large left', areas: [[0, 1, 2], [0, 3, 4], [0, 5, 5]] },
  { id: 'six-top', label: 'Large top', areas: [[0, 0, 0], [1, 2, 3], [4, 5, 5]] },
].map(layout => ({ ...layout, count: new Set(layout.areas.flat()).size }));
export const LAYOUT_KEY = 'alphaforge.chart.layout.v1';
export const defaultLayout = (configs = defaultIndicators()) => ({ version: 1, layout: 'one', active: 0, syncSymbol: false, syncCrosshair: false,
  tiles: Array.from({ length: 6 }, (_, i) => ({ instrument: INDEX_SHORTCUTS[i % INDEX_SHORTCUTS.length], timeframe: '1m', configs: JSON.parse(JSON.stringify(configs)), analysis: defaultAnalysisSettings() })) });
const configsValid = configs => saveIndicatorSettings({ setItem() {} }, configs).ok;
export function readLayout(storage, initialConfigs) {
  const fallback = defaultLayout(initialConfigs);
  try {
    const raw = storage.getItem(LAYOUT_KEY);
    if (!raw) return { state: fallback, writable: true, warning: '' };
    const state = JSON.parse(raw);
    const layout = LAYOUTS.find(item => item.id === state.layout);
    const valid = state.version === 1 && layout && Number.isInteger(state.active) && state.active >= 0 && state.active < layout.count &&
      (state.watchlistVisible === undefined || typeof state.watchlistVisible === 'boolean') && typeof state.syncSymbol === 'boolean' && typeof state.syncCrosshair === 'boolean' && Array.isArray(state.tiles) && state.tiles.length === 6 &&
      state.tiles.every(tile => tile && tile.instrument && typeof tile.instrument.instrument_key === 'string' && tile.instrument.instrument_key.length > 0 &&
        typeof tile.instrument.symbol === 'string' && TIMEFRAMES.includes(tile.timeframe) && configsValid(tile.configs) && normalizeAnalysisSettings(tile.analysis));
    if (!valid) throw new Error('invalid layout');
    return { state: { ...state, tiles: state.tiles.map(tile => ({ ...tile, analysis: normalizeAnalysisSettings(tile.analysis) })) }, writable: true, warning: '' };
  } catch { return { state: fallback, writable: false, warning: 'Saved chart layout could not be read; it was left untouched. Changes stay in this tab.' }; }
}
export function saveLayout(storage, state) {
  try {
    const raw = JSON.stringify(state);
    if (!readLayout({ getItem: () => raw }).writable) return 'Chart layout contains invalid settings; previous saved state was left untouched.';
    storage.setItem(LAYOUT_KEY, raw); return '';
  }
  catch { return 'Chart layout could not be saved. Changes stay in this tab.'; }
}
export const gridTemplate = layout => layout.areas.map(row => `"${row.map(i => `tile${i}`).join(' ')}"`).join(' ');
