import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * useInteractiveColumns — shared drag-to-resize + drag-to-reorder column
 * layout for the app's hand-rolled `<table>` blotters (paper TradeBlotter,
 * live LiveBlotter, BacktestLab's TradesTable).
 *
 * Purely a presentation-layer concern: it never touches row data, sort state,
 * filter state, or any P&L/trading computation — it only decides the DISPLAY
 * width/order of whatever column definitions the caller passes in, and
 * persists that layout to localStorage keyed by `tableId` so each table's
 * layout is independent.
 *
 * Usage:
 *   const {
 *     orderedColumns,      // fresh `columns`, reordered + widened per persisted layout
 *     getHeaderProps,      // (key) => props to spread onto the <th> (drag handlers, style)
 *     getResizeHandleProps,// (key) => props to spread onto a resize handle element
 *     resetLayout,         // clears persisted layout, reverts to defaults
 *     isCustomized,        // true if a persisted layout is currently active
 *   } = useInteractiveColumns({ tableId: "paper-blotter", columns, defaultWidth: 120 });
 *
 * `columns` is a fresh array of `{ key, ...anything }` on every render (e.g.
 * TradesTable computes it from the actual data shape). The persisted
 * `{key, width}[]` (in persisted order) is merged onto whatever `columns`
 * looks like THIS render: entries whose key no longer exists are dropped,
 * and any new key not yet in the persisted order is appended to the end —
 * so switching between datasets with a different column set never crashes
 * or silently loses a new column.
 */

const STORAGE_PREFIX = "af.table.";
const MIN_COL_WIDTH = 40;

function storageKey(tableId) {
  return `${STORAGE_PREFIX}${tableId}.columns`;
}

function loadPersisted(tableId) {
  if (typeof window === "undefined" || !tableId) return null;
  try {
    const raw = window.localStorage.getItem(storageKey(tableId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((e) => e && typeof e.key === "string");
  } catch {
    return null;
  }
}

function savePersisted(tableId, layout) {
  if (typeof window === "undefined" || !tableId) return;
  try {
    window.localStorage.setItem(storageKey(tableId), JSON.stringify(layout));
  } catch {
    // localStorage can throw (quota/private mode) — layout customization is
    // best-effort UI sugar, never load-bearing, so silently skip persistence.
  }
}

function clearPersisted(tableId) {
  if (typeof window === "undefined" || !tableId) return;
  try {
    window.localStorage.removeItem(storageKey(tableId));
  } catch {
    // best-effort, see savePersisted
  }
}

/**
 * Merge a persisted `{key, width}[]` (in persisted order) onto a fresh
 * `columns` array, by key. Drops persisted entries whose key no longer
 * exists in `columns`; appends any `columns` keys not yet in the persisted
 * order to the end (in their original relative order).
 */
function mergeLayout(columns, persisted, defaultWidth) {
  const byKey = new Map(columns.map((c) => [c.key, c]));
  const result = [];
  const seen = new Set();
  if (persisted) {
    for (const entry of persisted) {
      const col = byKey.get(entry.key);
      if (!col || seen.has(entry.key)) continue; // stale key — drop
      seen.add(entry.key);
      result.push({ ...col, width: Number.isFinite(entry.width) ? entry.width : (col.defaultWidth ?? defaultWidth) });
    }
  }
  for (const col of columns) {
    if (seen.has(col.key)) continue;
    result.push({ ...col, width: col.defaultWidth ?? defaultWidth });
  }
  return result;
}

export function useInteractiveColumns({ tableId, columns, defaultWidth = 120 }) {
  const [persisted, setPersisted] = useState(() => loadPersisted(tableId));

  // Re-read from storage if tableId changes (e.g. a table instance reused for
  // a different logical table id).
  useEffect(() => {
    setPersisted(loadPersisted(tableId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId]);

  const orderedColumns = useMemo(
    () => mergeLayout(columns, persisted, defaultWidth),
    [columns, persisted, defaultWidth]
  );

  const isCustomized = !!persisted && persisted.length > 0;

  const persistLayout = useCallback(
    (cols) => {
      const layout = cols.map((c) => ({ key: c.key, width: c.width }));
      savePersisted(tableId, layout);
      setPersisted(layout);
    },
    [tableId]
  );

  const resetLayout = useCallback(() => {
    clearPersisted(tableId);
    setPersisted(null);
  }, [tableId]);

  // ---- Drag-to-resize --------------------------------------------------
  const resizeRef = useRef(null); // { key, startX, startWidth }

  const onResizeMove = useCallback(
    (e) => {
      const st = resizeRef.current;
      if (!st) return;
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const delta = clientX - st.startX;
      const nextWidth = Math.max(MIN_COL_WIDTH, Math.round(st.startWidth + delta));
      st.liveWidth = nextWidth;
      if (st.onLiveResize) st.onLiveResize(st.key, nextWidth);
    },
    []
  );

  const onResizeEnd = useCallback(() => {
    const st = resizeRef.current;
    if (!st) return;
    document.removeEventListener("mousemove", onResizeMove);
    document.removeEventListener("mouseup", onResizeEnd);
    document.removeEventListener("touchmove", onResizeMove);
    document.removeEventListener("touchend", onResizeEnd);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const finalWidth = st.liveWidth ?? st.startWidth;
    const next = orderedColumns.map((c) => (c.key === st.key ? { ...c, width: finalWidth } : c));
    persistLayout(next);
    resizeRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onResizeMove, orderedColumns, persistLayout]);

  const startResize = useCallback(
    (key) => (e) => {
      e.preventDefault();
      e.stopPropagation();
      const col = orderedColumns.find((c) => c.key === key);
      const startX = e.touches ? e.touches[0].clientX : e.clientX;
      resizeRef.current = { key, startX, startWidth: col?.width ?? defaultWidth, liveWidth: null };
      document.addEventListener("mousemove", onResizeMove);
      document.addEventListener("mouseup", onResizeEnd);
      document.addEventListener("touchmove", onResizeMove, { passive: false });
      document.addEventListener("touchend", onResizeEnd);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [orderedColumns, defaultWidth, onResizeMove, onResizeEnd]
  );

  const getResizeHandleProps = useCallback(
    (key) => ({
      onMouseDown: startResize(key),
      onTouchStart: startResize(key),
      role: "separator",
      "aria-orientation": "vertical",
      title: "Drag to resize column",
      "data-testid": `col-resize-${key}`,
    }),
    [startResize]
  );

  // ---- Drag-to-reorder ---------------------------------------------------
  const dragKeyRef = useRef(null);
  const [dragOverKey, setDragOverKey] = useState(null);

  const reorder = useCallback(
    (fromKey, toKey) => {
      if (!fromKey || !toKey || fromKey === toKey) return;
      const cur = [...orderedColumns];
      const fromIdx = cur.findIndex((c) => c.key === fromKey);
      const toIdx = cur.findIndex((c) => c.key === toKey);
      if (fromIdx === -1 || toIdx === -1) return;
      const [moved] = cur.splice(fromIdx, 1);
      cur.splice(toIdx, 0, moved);
      persistLayout(cur);
    },
    [orderedColumns, persistLayout]
  );

  const getHeaderProps = useCallback(
    (key) => ({
      draggable: true,
      onDragStart: (e) => {
        dragKeyRef.current = key;
        e.dataTransfer.effectAllowed = "move";
        try {
          e.dataTransfer.setData("text/plain", key);
        } catch {
          // some browsers require setData for drag to initiate at all; ignore failures
        }
      },
      onDragEnter: (e) => {
        e.preventDefault();
        if (dragKeyRef.current && dragKeyRef.current !== key) setDragOverKey(key);
      },
      onDragOver: (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      },
      onDragLeave: () => {
        setDragOverKey((k) => (k === key ? null : k));
      },
      onDrop: (e) => {
        e.preventDefault();
        const fromKey = dragKeyRef.current;
        setDragOverKey(null);
        dragKeyRef.current = null;
        reorder(fromKey, key);
      },
      onDragEnd: () => {
        dragKeyRef.current = null;
        setDragOverKey(null);
      },
      "data-drag-over": dragOverKey === key ? "true" : undefined,
      "data-testid": `col-header-${key}`,
    }),
    [reorder, dragOverKey]
  );

  return {
    orderedColumns,
    getHeaderProps,
    getResizeHandleProps,
    resetLayout,
    isCustomized,
  };
}
