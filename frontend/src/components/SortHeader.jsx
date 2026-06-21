import { useState } from "react";
import { ChevronsUpDown, ArrowUp, ArrowDown } from "lucide-react";

/**
 * Reusable click-to-sort for table panes (mirrors the Trades table's pattern).
 * `useTableSort` holds the {key,dir} state + a stable toggle; `sortRows(rows,
 * getValue)` returns a sorted copy (numeric when both values parse as numbers,
 * else locale string compare). `getValue(row, key)` lets callers map a sort key
 * to a nested field. With no active key the rows pass through unchanged.
 */
export function useTableSort(initialKey = null, initialDir = "asc") {
  const [sort, setSort] = useState({ key: initialKey, dir: initialDir });
  const onSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  const sortRows = (rows, getValue) => {
    if (!sort.key) return rows;
    const get = getValue || ((row, key) => row[key]);
    const mul = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = get(a, sort.key);
      const bv = get(b, sort.key);
      const an = typeof av === "number" ? av : Number(av);
      const bn = typeof bv === "number" ? bv : Number(bv);
      if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * mul;
      return String(av ?? "").localeCompare(String(bv ?? "")) * mul;
    });
  };

  return { sort, onSort, sortRows };
}

/** A sortable `<th>` — `col = { key, label, align?, sortable? }`. */
export function SortHeader({ col, sort, onSort, testidPrefix = "sort" }) {
  const active = sort.key === col.key;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  const alignCls = col.align === "right" ? "text-right" : "text-left";
  if (col.sortable === false) {
    return <th className={`${alignCls} p-2`}>{col.label}</th>;
  }
  return (
    <th className={`${alignCls} p-2`}>
      <button
        type="button"
        onClick={() => onSort(col.key)}
        className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${active ? "text-foreground" : ""} ${col.align === "right" ? "flex-row-reverse" : ""}`}
        data-testid={`${testidPrefix}-${col.key}`}
        title={`Sort by ${col.label}`}
      >
        <span>{col.label}</span>
        <Icon className={`w-3 h-3 ${active ? "text-info" : "text-dimmer"}`} />
      </button>
    </th>
  );
}
