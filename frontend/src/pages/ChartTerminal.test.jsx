import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ChartTerminal from './ChartTerminal';
import { LAYOUT_KEY } from '../lib/chartLayouts';
const mockTrading = jest.fn();
jest.mock('../components/charts/useChartTrading', () => instrument => { mockTrading(instrument.instrument_key); return { rows: [], mode: 'paper', stale: false, requestChange: jest.fn() }; });
jest.mock('../components/charts/ChartTrading', () => ({ instrument }) => <div data-testid="trade-context">{instrument.symbol}</div>);
jest.mock('../components/charts/ChartPositions', () => () => null);
jest.mock('../components/charts/InstrumentSearch', () => ({ onSelect }) => <button onClick={() => onSelect({ instrument_key: 'CUSTOM', symbol: 'CUSTOM', exchange: 'NSE', kind: 'index' })}>Custom</button>);
jest.mock('../components/charts/ChartTile', () => ({ tile, index, active, onActivate, onChange }) => <div data-testid={`mock-tile-${index}`} onClick={onActivate}>{tile.instrument.symbol}/{tile.timeframe}/{String(active)}<button onClick={() => onChange({ timeframe:'1d' })}>Day</button></div>);
let container, root;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; localStorage.clear(); mockTrading.mockClear(); container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
test('active tile controls the single shared trading context and settings persist across reload', async () => {
 await act(async () => root.render(<ChartTerminal />)); await act(async () => container.querySelector('.chart-layout-picker > button').click()); await act(async () => container.querySelector('[aria-label="2 charts: Columns"]').click());
 expect(container.querySelector('[data-testid="trade-context"]').textContent).toBe('NIFTY');
 await act(async () => container.querySelector('[data-testid="mock-tile-1"]').click());
 expect(container.querySelector('[data-testid="trade-context"]').textContent).toBe('BANKNIFTY');
 await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Custom').click());
 expect(container.querySelector('[data-testid="trade-context"]').textContent).toBe('CUSTOM');
 await act(async () => container.querySelector('[data-testid="mock-tile-1"] button').click());
 const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY)); expect(saved.tiles[0].timeframe).toBe('1m'); expect(saved.tiles[1].timeframe).toBe('1d');
 await act(async () => root.unmount()); root = createRoot(container); await act(async () => root.render(<ChartTerminal />));
 expect(container.querySelector('[data-testid="trade-context"]').textContent).toBe('CUSTOM'); expect(container.querySelector('[data-testid="mock-tile-1"]').textContent).toContain('1d');
});

test('maximizing retains search, selected layouts and the order panel within full screen', async () => {
 await act(async () => root.render(<ChartTerminal />));
 const surface = container.querySelector('[data-testid="chart-terminal"]');
 surface.requestFullscreen = jest.fn(async () => {
   Object.defineProperty(document, 'fullscreenElement', {configurable:true, value:surface});
   document.dispatchEvent(new Event('fullscreenchange'));
 });
 document.exitFullscreen = jest.fn(async () => {
   Object.defineProperty(document, 'fullscreenElement', {configurable:true, value:null});
   document.dispatchEvent(new Event('fullscreenchange'));
 });
 await act(async () => container.querySelector('[aria-label="Maximize chart layout"]').click());
 expect(surface.requestFullscreen).toHaveBeenCalledTimes(1);
 expect(surface.querySelector('[data-testid="trade-context"]')).not.toBeNull();
 expect([...surface.querySelectorAll('button')].some(button => button.textContent === 'Custom')).toBe(true);
 await act(async () => container.querySelector('[aria-label="Exit full screen"]').click());
 expect(document.exitFullscreen).toHaveBeenCalledTimes(1);
 expect(container.querySelector('[aria-label="Maximize chart layout"]')).not.toBeNull();
});

test('browser fullscreen rejection falls back to a maximized workspace and Escape restores it', async () => {
 await act(async () => root.render(<ChartTerminal />));
 const surface = container.querySelector('[data-testid="chart-terminal"]');
 surface.requestFullscreen = jest.fn(async () => { throw new Error('Permissions check failed'); });
 await act(async () => container.querySelector('[aria-label="Maximize chart layout"]').click());
 expect(surface.classList.contains('chart-terminal-window-maximized')).toBe(true);
 expect(document.body.style.overflow).toBe('hidden');
 expect(surface.querySelector('[data-testid="trade-context"]')).not.toBeNull();
 await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape'})));
 expect(surface.classList.contains('chart-terminal-window-maximized')).toBe(false);
 expect(document.body.style.overflow).toBe('');
});
