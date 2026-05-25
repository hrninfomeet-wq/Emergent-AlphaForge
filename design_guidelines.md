{
  "product": {
    "name": "AlphaForge Trading Lab",
    "design_personality": [
      "Bloomberg/TradingView-grade dark cockpit",
      "information-dense but never cluttered",
      "terminal-inspired restraint (no glass, no gradients, no transparency)",
      "statistically honest + audit-trail-first",
      "fast scanning: alignment, tabular numerals, compact rows"
    ],
    "north_star": "Every pixel must justify its existence. Optimize for speed of interpretation under stress (market hours)."
  },

  "global_rules": {
    "no_transparency": true,
    "no_glassmorphism": true,
    "no_playful_icons": true,
    "no_large_gradients": true,
    "density": "high",
    "testing": {
      "data_testid_required": "All interactive and key informational elements MUST include data-testid in kebab-case (role-based, not appearance-based)."
    }
  },

  "inspiration_refs": {
    "visual": [
      {
        "name": "TradingView Lightweight Charts docs (layout + crosshair patterns)",
        "url": "https://tradingview.github.io/lightweight-charts/docs/api/interfaces/LayoutOptions"
      },
      {
        "name": "Dribbble: dark mode dashboard patterns (tables, dense cards)",
        "url": "https://dribbble.com/tags/dark-mode-dashboard"
      },
      {
        "name": "Amber-on-black terminal aesthetic discussion",
        "url": "https://ted-merz.com/2021/06/26/amber-on-black/"
      }
    ],
    "ux": [
      {
        "name": "NN/g: Dark Mode users issues (contrast + fatigue)",
        "url": "https://www.nngroup.com/articles/dark-mode-users-issues/"
      }
    ]
  },

  "typography": {
    "font_pairing": {
      "ui_sans": {
        "recommended": "IBM Plex Sans",
        "fallback": "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        "usage": "All UI labels, headings, navigation, form labels"
      },
      "ui_mono": {
        "recommended": "IBM Plex Mono",
        "fallback": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        "usage": "All numbers: prices, P&L, % change, timestamps, strategy IDs, metrics"
      }
    },
    "implementation_notes": {
      "google_fonts": {
        "add_to_index_html": [
          "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
        ]
      },
      "tailwind_usage": {
        "body": "font-sans",
        "numbers": "font-mono tabular-nums",
        "numeric_alignment": "Use text-right for numeric columns; keep +/- sign always visible"
      }
    },
    "type_scale": {
      "page_title_h1": "text-2xl lg:text-3xl font-semibold tracking-tight",
      "section_title_h2": "text-sm lg:text-base font-semibold text-foreground",
      "panel_title": "text-xs font-semibold uppercase tracking-wider text-muted-foreground",
      "body": "text-sm leading-5 text-foreground",
      "meta": "text-xs text-muted-foreground",
      "table": {
        "header": "text-xs font-medium text-muted-foreground",
        "cell": "text-xs lg:text-sm",
        "numeric_cell": "text-xs lg:text-sm font-mono tabular-nums"
      }
    }
  },

  "color_system": {
    "strategy": "Use layered dark neutrals for surfaces; reserve saturated colors for semantic states and chart series. Avoid pure black (#000) and pure white (#fff).",
    "tokens_css_variables": {
      "note": "Main agent should map these into /app/frontend/src/index.css under .dark (HSL tokens used by shadcn). Values below are HEX references; convert to HSL for shadcn tokens.",
      "neutrals_hex": {
        "bg_0": "#0B0F14",
        "bg_1": "#11161D",
        "bg_2": "#161C24",
        "bg_3": "#1B2330",
        "border_1": "#263041",
        "border_2": "#314055",
        "text_1": "#E6EAF2",
        "text_2": "#AAB4C5",
        "text_3": "#6E7A8E"
      },
      "semantic_hex": {
        "success": "#2ED47A",
        "danger": "#FF5D5D",
        "warning": "#F5B84B",
        "info": "#5AA9FF",
        "focus_ring": "#7C8CFF"
      },
      "terminal_accent_hex": {
        "amber": "#F2C14E",
        "amber_dim": "#B8922E"
      },
      "chart_palette_hex": {
        "series_1": "#5AA9FF",
        "series_2": "#7C8CFF",
        "series_3": "#2ED47A",
        "series_4": "#F5B84B",
        "series_5": "#FF5D5D"
      }
    },
    "semantic_usage": {
      "profit_positive": {
        "color": "success",
        "secondary_indicator": "ArrowUp icon + leading '+' sign",
        "tailwind": "text-emerald-400"
      },
      "loss_negative": {
        "color": "danger",
        "secondary_indicator": "ArrowDown icon + leading '-' sign",
        "tailwind": "text-rose-400"
      },
      "warning": {
        "color": "warning",
        "secondary_indicator": "TriangleAlert icon",
        "tailwind": "text-amber-300"
      },
      "info": {
        "color": "info",
        "secondary_indicator": "Info icon",
        "tailwind": "text-sky-400"
      }
    },
    "accessibility_color_blind": {
      "rule": "Never rely on red/green alone. Always pair with sign (+/-), arrow icon, and/or label (PROFIT/LOSS).",
      "heatmap": "Prefer luminance ramps (dark->light) with a single hue or blue->neutral->amber diverging. Avoid red/green ramps."
    }
  },

  "layout_and_grid": {
    "desktop_shell": {
      "pattern": "Left rail + top command bar + multi-pane workspace",
      "recommended": {
        "left_nav_width": "w-[260px]",
        "topbar_height": "h-12",
        "content_max": "max-w-none (full width)",
        "page_padding": "px-3 lg:px-4 py-3",
        "gaps": "gap-3"
      },
      "tailwind_scaffold": "min-h-dvh bg-[var(--bg-0)] text-foreground"
    },
    "multi_pane_backtest": {
      "goal": "Synchronized time axis across 3 stacked charts: Price (top), Equity (middle), Drawdown (bottom).",
      "pane_heights": {
        "price": "h-[360px] lg:h-[420px]",
        "equity": "h-[180px] lg:h-[220px]",
        "drawdown": "h-[140px] lg:h-[180px]"
      },
      "resizable": {
        "use": "shadcn Resizable panels",
        "component_path": "/app/frontend/src/components/ui/resizable.jsx",
        "notes": "Allow user to drag pane heights; persist in localStorage."
      }
    },
    "density_principles": [
      "Prefer 12-column grid on desktop; 4-column on mobile",
      "Align numbers right; align labels left",
      "Use separators and subtle surface shifts instead of big card shadows",
      "Keep primary actions in a consistent top-right cluster"
    ]
  },

  "components": {
    "shadcn_primary_component_paths": {
      "button": "/app/frontend/src/components/ui/button.jsx",
      "card": "/app/frontend/src/components/ui/card.jsx",
      "badge": "/app/frontend/src/components/ui/badge.jsx",
      "tabs": "/app/frontend/src/components/ui/tabs.jsx",
      "table": "/app/frontend/src/components/ui/table.jsx",
      "select": "/app/frontend/src/components/ui/select.jsx",
      "checkbox": "/app/frontend/src/components/ui/checkbox.jsx",
      "switch": "/app/frontend/src/components/ui/switch.jsx",
      "slider": "/app/frontend/src/components/ui/slider.jsx",
      "popover": "/app/frontend/src/components/ui/popover.jsx",
      "tooltip": "/app/frontend/src/components/ui/tooltip.jsx",
      "scroll_area": "/app/frontend/src/components/ui/scroll-area.jsx",
      "skeleton": "/app/frontend/src/components/ui/skeleton.jsx",
      "dialog": "/app/frontend/src/components/ui/dialog.jsx",
      "sheet": "/app/frontend/src/components/ui/sheet.jsx",
      "pagination": "/app/frontend/src/components/ui/pagination.jsx",
      "calendar": "/app/frontend/src/components/ui/calendar.jsx",
      "sonner_toast": "/app/frontend/src/components/ui/sonner.jsx"
    },

    "buttons": {
      "style": "Professional/corporate: medium radius, flat tonal fills, minimal shadow. No gradients.",
      "tokens": {
        "radius": "rounded-md",
        "height": "h-9",
        "padding": "px-3",
        "font": "text-sm font-medium",
        "focus": "focus-visible:ring-2 focus-visible:ring-[color:var(--focus-ring)] focus-visible:ring-offset-0"
      },
      "variants": {
        "primary": {
          "use_for": "Run Backtest, Start Optimizer, Deploy Paper Trade",
          "tailwind": "bg-[var(--bg-3)] text-foreground border border-[color:var(--border-1)] hover:bg-[#202A39] active:bg-[#243044]",
          "data_testid_examples": [
            "backtest-run-button",
            "optimizer-start-button"
          ]
        },
        "secondary": {
          "use_for": "Load Preset, Export CSV",
          "tailwind": "bg-[var(--bg-2)] text-[color:var(--text-2)] border border-[color:var(--border-1)] hover:text-foreground hover:bg-[var(--bg-3)]",
          "data_testid_examples": [
            "backtest-load-preset-button",
            "export-trades-csv-button"
          ]
        },
        "ghost": {
          "use_for": "Icon actions in tables",
          "tailwind": "bg-transparent hover:bg-[var(--bg-3)]",
          "data_testid_examples": [
            "trade-row-more-actions-button"
          ]
        },
        "danger": {
          "use_for": "Delete strategy, revoke token",
          "tailwind": "bg-[#2A1416] text-rose-200 border border-[#4A1F24] hover:bg-[#34181B]",
          "data_testid_examples": [
            "strategy-delete-button"
          ]
        }
      }
    },

    "cards_and_panels": {
      "base_card": {
        "tailwind": "bg-[var(--bg-1)] border border-[color:var(--border-1)] rounded-lg",
        "header": "px-3 py-2 border-b border-[color:var(--border-1)]",
        "content": "p-3",
        "title": "text-sm font-semibold",
        "subtitle": "text-xs text-muted-foreground"
      },
      "panel_header_pattern": "Left: title + meta; Right: compact actions (buttons, density toggle, export)."
    },

    "badges": {
      "regime_indicator": {
        "component": "Badge",
        "placement": "Top bar, always visible",
        "states": {
          "TREND_UP": {
            "tailwind": "bg-emerald-950 text-emerald-200 border border-emerald-900",
            "icon": "TrendingUp (lucide-react)",
            "data_testid": "regime-indicator-badge"
          },
          "TREND_DOWN": {
            "tailwind": "bg-rose-950 text-rose-200 border border-rose-900",
            "icon": "TrendingDown",
            "data_testid": "regime-indicator-badge"
          },
          "CHOP": {
            "tailwind": "bg-slate-900 text-slate-200 border border-slate-700",
            "icon": "Shuffle",
            "data_testid": "regime-indicator-badge"
          },
          "VOLATILE": {
            "tailwind": "bg-amber-950 text-amber-200 border border-amber-900",
            "icon": "Activity",
            "data_testid": "regime-indicator-badge"
          }
        }
      },
      "statistical_significance": {
        "rule": "Use traffic-light badge + explicit label (SIGNIFICANT / BORDERLINE / WEAK). Do not use emoji in UI.",
        "states": {
          "strong": {
            "tailwind": "bg-emerald-950 text-emerald-200 border border-emerald-900",
            "icon": "CheckCircle2",
            "label": "SIGNIFICANT",
            "data_testid": "backtest-significance-badge"
          },
          "medium": {
            "tailwind": "bg-amber-950 text-amber-200 border border-amber-900",
            "icon": "AlertCircle",
            "label": "BORDERLINE",
            "data_testid": "backtest-significance-badge"
          },
          "weak": {
            "tailwind": "bg-rose-950 text-rose-200 border border-rose-900",
            "icon": "XCircle",
            "label": "WEAK",
            "data_testid": "backtest-significance-badge"
          }
        }
      }
    },

    "tables": {
      "density_modes": {
        "compact": {
          "row": "h-7",
          "cell": "py-1 px-2",
          "text": "text-xs"
        },
        "default": {
          "row": "h-9",
          "cell": "py-2 px-3",
          "text": "text-sm"
        },
        "comfort": {
          "row": "h-11",
          "cell": "py-3 px-3",
          "text": "text-sm"
        }
      },
      "patterns": [
        "Sticky header: bg-[var(--bg-2)] with border-b",
        "Row hover: bg-[var(--bg-3)]",
        "Selected row: bg-[#202A39] + left accent bar (2px) using info color",
        "Numeric columns right-aligned + font-mono tabular-nums",
        "Sort affordance: chevron icon + aria-sort"
      ],
      "tailwind_snippet": {
        "table_wrapper": "rounded-lg border border-[color:var(--border-1)] overflow-hidden",
        "thead": "sticky top-0 z-10 bg-[var(--bg-2)]",
        "tr_hover": "hover:bg-[var(--bg-3)]",
        "td_numeric": "text-right font-mono tabular-nums"
      },
      "data_testid_examples": [
        "signal-journal-table",
        "signal-journal-table-row",
        "signal-journal-sort-button"
      ]
    },

    "filters_and_forms": {
      "filter_panel": {
        "pattern": "Left-side filter rail (desktop) with collapsible sections; on mobile becomes Sheet.",
        "components": [
          "Collapsible",
          "Select",
          "Checkbox",
          "Switch",
          "Slider",
          "Tabs"
        ],
        "live_counter": {
          "description": "Always show 'Signals passing' counter pinned at bottom of filter panel.",
          "tailwind": "sticky bottom-0 bg-[var(--bg-1)] border-t border-[color:var(--border-1)] p-3",
          "data_testid": "pretrade-signals-passing-counter"
        },
        "preset_profiles": {
          "ui": "Tabs: Conservative / Balanced / Aggressive",
          "data_testid": "pretrade-profile-tabs"
        }
      },
      "plugin_upload": {
        "pattern": "Dropzone-style Card with dashed border; show validation + checksum.",
        "tailwind": "border border-dashed border-[color:var(--border-2)] bg-[var(--bg-2)] rounded-lg p-4",
        "data_testid": "strategy-plugin-upload-dropzone"
      }
    },

    "charts": {
      "lightweight_charts": {
        "rules": [
          "No heavy shadows; use crisp grid lines",
          "Crosshair must sync across panes",
          "Use muted grid: border_1 color",
          "Candles: up=success, down=danger; wicks slightly dimmer",
          "Volume bars: neutral with slight tint"
        ],
        "layout_options": {
          "background": "solid bg_1",
          "textColor": "text_2",
          "grid": "border_1",
          "crosshair": "text_3"
        },
        "performance": "Prefer canvas rendering defaults; avoid excessive markers; throttle live updates."
      },
      "recharts": {
        "use_cases": [
          "Coverage heatmap (months × instruments)",
          "Optimizer parameter importance (bar)",
          "Robustness scatter / walk-forward comparison"
        ],
        "styling": "Use same chart palette tokens; tooltips must be opaque (no transparency)."
      }
    },

    "specialized_views": {
      "signal_funnel": {
        "description": "Funnel visualization: stages with counts + drop-off %. Use horizontal segmented bar (not a marketing funnel).",
        "implementation": "Recharts BarChart with stacked segments; each segment labeled with stage name + count.",
        "tailwind_container": "bg-[var(--bg-1)] border border-[color:var(--border-1)] rounded-lg p-3",
        "data_testid": "signal-funnel-chart"
      },
      "coverage_heatmap": {
        "description": "Months × instruments grid. Each cell shows coverage % and integrity status (dot + tooltip).",
        "palette": "Use blue->neutral->amber ramp; avoid red/green ramp.",
        "interaction": "Hover shows tooltip with missing days, bad candles, last ingest timestamp.",
        "data_testid": "data-coverage-heatmap"
      },
      "live_signal_card": {
        "goal": "A complete trade plan in one card; scannable in 3 seconds.",
        "layout": {
          "header": "Instrument + expiry + strike + direction + timestamp",
          "body_grid": "2-column on mobile, 4-column on desktop",
          "footer": "Actions: Add to paper trade, Acknowledge, Open chart"
        },
        "sections": [
          "Entry / Target1 / Target2 / Stop / Time stop",
          "Probability distribution mini (sparkline or histogram)",
          "Regime + VIX + News risk badges",
          "Expected value + R multiple",
          "Invalidation level (explicit)"
        ],
        "tailwind": {
          "card": "bg-[var(--bg-1)] border border-[color:var(--border-1)] rounded-lg p-3",
          "key_price": "text-base font-mono tabular-nums",
          "label": "text-[11px] uppercase tracking-wider text-muted-foreground",
          "divider": "border-t border-[color:var(--border-1)] my-2"
        },
        "mobile_rules": [
          "Never exceed 2 columns; keep key prices above the fold",
          "Use collapsible 'Context' section for regime/news/probability details",
          "Primary action is a full-width button at bottom"
        ],
        "data_testid_examples": [
          "live-signal-card",
          "live-signal-entry-price",
          "live-signal-add-to-paper-button",
          "live-signal-open-chart-button"
        ]
      }
    },

    "empty_and_loading_states": {
      "empty": {
        "pattern": "Explain why empty + provide 1 primary action + 1 secondary action. No illustrations.",
        "example": "No backtest run yet — load a preset or configure a strategy.",
        "tailwind": "bg-[var(--bg-1)] border border-dashed border-[color:var(--border-2)] rounded-lg p-6 text-sm text-muted-foreground",
        "data_testid": "empty-state"
      },
      "loading": {
        "pattern": "Use Skeleton blocks matching final layout (charts + table rows). Avoid spinners.",
        "component": "Skeleton",
        "data_testid": "loading-skeleton"
      }
    }
  },

  "motion": {
    "principles": [
      "Subtle, fast, functional",
      "No bouncy easing",
      "No continuous animations except live tick flash"
    ],
    "tokens": {
      "fast": "duration-150",
      "base": "duration-200",
      "panel": "duration-250",
      "easing": "ease-out"
    },
    "allowed_microinteractions": [
      "Row hover tint",
      "Button press scale: active:scale-[0.98]",
      "Tooltip fade-in",
      "Live price tick flash: brief background pulse on updated cells"
    ],
    "reduced_motion": "Respect prefers-reduced-motion: disable tick flash and panel transitions."
  },

  "accessibility": {
    "wcag": "AA",
    "focus": "Always visible focus ring (info/focus_ring color).",
    "keyboard": "All tables and filter controls must be keyboard navigable.",
    "color": "Never encode meaning with color alone; pair with icons/labels/signs.",
    "tooltips": "Use Tooltip for truncated values; ensure tooltip content is readable and not transparent."
  },

  "responsive": {
    "breakpoints": {
      "mobile": "<640px",
      "tablet": "640-1024px",
      "desktop": ">=1024px"
    },
    "mobile_signal_cards": {
      "rules": [
        "2-column grid max",
        "Primary action sticky at bottom",
        "Use Sheet for filters",
        "Avoid dense tables; use cards + pagination"
      ]
    }
  },

  "libraries": {
    "icons": {
      "use": "lucide-react (already typical with shadcn)",
      "rule": "Do not use emoji icons."
    },
    "charts": {
      "primary": "TradingView lightweight-charts",
      "secondary": "Recharts",
      "notes": "Ensure chart containers have fixed heights; avoid reflow on data updates."
    },
    "motion_optional": {
      "library": "framer-motion",
      "use_cases": [
        "Panel expand/collapse",
        "Subtle list entrance for signal cards"
      ],
      "install": "npm i framer-motion",
      "rule": "Keep motion minimal; do not animate charts."
    }
  },

  "image_urls": {
    "rule": "This is a tool UI; avoid decorative photography. Use no images by default.",
    "categories": [
      {
        "category": "empty_states",
        "description": "No illustrations; use icon + text only.",
        "urls": []
      }
    ]
  },

  "instructions_to_main_agent": {
    "theme": [
      "Replace current shadcn default tokens in /app/frontend/src/index.css .dark with the provided layered neutrals + semantic colors (convert HEX to HSL).",
      "Remove any centered layout defaults from App.css; App.css currently centers .App-header—do not use that pattern for the dashboard.",
      "Use font-sans for UI and font-mono tabular-nums for all numeric values.",
      "Implement density toggle for tables (compact/default/comfort) and persist user choice.",
      "All interactive and key informational elements must include data-testid attributes (kebab-case)."
    ],
    "page_level_layout": [
      "Desktop: left navigation rail + top command bar + content panes.",
      "Backtest Lab: Resizable vertical stack for 3 synchronized charts + right-side metrics panel + bottom trade table.",
      "Mobile: prioritize Live Signals cards; move filters into Sheet; avoid dense tables."
    ],
    "component_building": [
      "Use shadcn components from /app/frontend/src/components/ui as primary primitives.",
      "No HTML-native dropdown/calendar/toast; use shadcn Select/Calendar/Sonner.",
      "No gradients except tiny decorative accents (but recommended: none for this product)."
    ]
  },

  "appendix_general_ui_ux_design_guidelines": "- You must **not** apply universal transition. Eg: `transition: all`. This results in breaking transforms. Always add transitions for specific interactive elements like button, input excluding transforms\n    - You must **not** center align the app container, ie do not add `.App { text-align: center; }` in the css file. This disrupts the human natural reading flow of text\n   - NEVER: use AI assistant Emoji characters like`🤖🧠💭💡🔮🎯📚🎭🎬🎪🎉🎊🎁🎀🎂🍰🎈🎨🎰💰💵💳🏦💎🪙💸🤑📊📈📉💹🔢🏆🥇 etc for icons. Always use **FontAwesome cdn** or **lucid-react** library already installed in the package.json\n\n **GRADIENT RESTRICTION RULE**\nNEVER use dark/saturated gradient combos (e.g., purple/pink) on any UI element.  Prohibited gradients: blue-500 to purple 600, purple 500 to pink-500, green-500 to blue-500, red to pink etc\nNEVER use dark gradients for logo, testimonial, footer etc\nNEVER let gradients cover more than 20% of the viewport.\nNEVER apply gradients to text-heavy content or reading areas.\nNEVER use gradients on small UI elements (<100px width).\nNEVER stack multiple gradient layers in the same viewport.\n\n**ENFORCEMENT RULE:**\n    • Id gradient area exceeds 20% of viewport OR affects readability, **THEN** use solid colors\n\n**How and where to use:**\n   • Section backgrounds (not content backgrounds)\n   • Hero section header content. Eg: dark to light to dark color\n   • Decorative overlays and accent elements only\n   • Hero section with 2-3 mild color\n   • Gradients creation can be done for any angle say horizontal, vertical or diagonal\n\n- For AI chat, voice application, **do not use purple color. Use color like light green, ocean blue, peach orange etc**\n\n</Font Guidelines>\n\n- Every interaction needs micro-animations - hover states, transitions, parallax effects, and entrance animations. Static = dead. \n   \n- Use 2-3x more spacing than feels comfortable. Cramped designs look cheap.\n\n- Subtle grain textures, noise overlays, custom cursors, selection states, and loading animations: separates good from extraordinary.\n   \n- Before generating UI, infer the visual style from the problem statement (palette, contrast, mood, motion) and immediately instantiate it by setting global design tokens (primary, secondary/accent, background, foreground, ring, state colors), rather than relying on any library defaults. Don't make the background dark as a default step, always understand problem first and define colors accordingly\n    Eg: - if it implies playful/energetic, choose a colorful scheme\n           - if it implies monochrome/minimal, choose a black–white/neutral scheme\n\n**Component Reuse:**\n\t- Prioritize using pre-existing components from src/components/ui when applicable\n\t- Create new components that match the style and conventions of existing components when needed\n\t- Examine existing components to understand the project's component patterns before creating new ones\n\n**IMPORTANT**: Do not use HTML based component like dropdown, calendar, toast etc. You **MUST** always use `/app/frontend/src/components/ui/ ` only as a primary components as these are modern and stylish component\n\n**Best Practices:**\n\t- Use Shadcn/UI as the primary component library for consistency and accessibility\n\t- Import path: ./components/[component-name]\n\n**Export Conventions:**\n\t- Components MUST use named exports (export const ComponentName = ...)\n\t- Pages MUST use default exports (export default function PageName() {...})\n\n**Toasts:**\n  - Use `sonner` for toasts\"\n  - Sonner component are located in `/app/src/components/ui/sonner.tsx`\n\nUse 2–4 color gradients, subtle textures/noise overlays, or CSS-based noise to avoid flat visuals."
}
