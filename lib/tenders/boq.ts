/**
 * The schedule of quantities (BOQ) the analysis read from the tender (backend/proposalpanda/engine/boq.py), and
 * the figures the BOQ panel derives from it.
 */

export interface BoqItem {
  /** Item number as printed, e.g. "1.4.1.1" or "7 (a)" */
  no: string;
  description: string;
  quantity: number;
  unit: string;
  /** The department's estimated rate, in rupees */
  rate: number;
  amount: number;
  /** Sub-head or section the item falls under, e.g. "Earth Work" */
  section?: string;
}

export interface BoqPart {
  /** Heading of the schedule, e.g. "Civil work" */
  title: string;
  items: BoqItem[];
  /** The total the tender prints at the end of this schedule, if any */
  statedTotal?: number;
}


export interface BoqInsights {
  itemCount: number;
  /** Sum of the amounts read */
  total: number;
  /** Sum of the totals the tender prints, when every schedule prints one */
  statedTotal?: number;
  /** Items that together make up 80% of the value, largest first */
  majorItems: BoqItem[];
  bySection: Array<{ section: string; amount: number; items: number }>;
  /** Rows where quantity × rate is more than 1% away from the amount printed */
  arithmetic: BoqItem[];
  lumpSum: BoqItem[];
}

export function boqInsights(parts: BoqPart[]): BoqInsights {
  const items = parts.flatMap(p => p.items);
  const total = items.reduce((sum, i) => sum + i.amount, 0);
  const sorted = [...items].sort((a, b) => b.amount - a.amount);
  const majorItems: BoqItem[] = [];
  let running = 0;
  for (const item of sorted) {
    if (running >= total * 0.8) break;
    majorItems.push(item);
    running += item.amount;
  }
  const sections = new Map<string, { amount: number; items: number }>();
  for (const part of parts) {
    for (const item of part.items) {
      const key = parts.length > 1 ? `${part.title}: ${item.section ?? 'Other items'}` : (item.section ?? 'Other items');
      const entry = sections.get(key) ?? { amount: 0, items: 0 };
      sections.set(key, { amount: entry.amount + item.amount, items: entry.items + 1 });
    }
  }
  return {
    itemCount: items.length,
    total,
    statedTotal: parts.every(p => p.statedTotal) ? parts.reduce((sum, p) => sum + p.statedTotal!, 0) : undefined,
    majorItems,
    bySection: [...sections].map(([section, v]) => ({ section, ...v })).sort((a, b) => b.amount - a.amount),
    arithmetic: items.filter(i => Math.abs(i.quantity * i.rate - i.amount) > Math.max(2, i.amount * 0.01)),
    lumpSum: items.filter(i => /^(?:l\.?s\.?|lump|job)/i.test(i.unit)),
  };
}

/** "Rs. 15,64,86,955/-" → 156486955 */
export function rupees(printed: string | undefined): number | undefined {
  const match = printed?.replace(/,/g, '').match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : undefined;
}
