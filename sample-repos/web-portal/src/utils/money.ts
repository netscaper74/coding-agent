// Money is always integer minor units in the front end (SCS-4.1).

export interface Money {
  minor: number; // integer, e.g. 1050 = 10.50 AED
  currency: string;
}

const MINOR_UNITS: Record<string, number> = { AED: 2, USD: 2, EUR: 2, GBP: 2, JPY: 0, KWD: 3 };

export function formatMoney(m: Money, locale = "en-AE"): string {
  const digits = MINOR_UNITS[m.currency] ?? 2;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: m.currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(m.minor / 10 ** digits);
}

export function parseAmount(input: string, currency: string): Money | null {
  const digits = MINOR_UNITS[currency];
  if (digits === undefined) return null;
  const pattern = digits === 0 ? /^\d+$/ : new RegExp(`^\\d+(\\.\\d{1,${digits}})?$`);
  const clean = input.trim().replace(/,/g, "");
  if (!pattern.test(clean)) return null;
  const [whole, frac = ""] = clean.split(".");
  return { minor: Number(whole) * 10 ** digits + Number(frac.padEnd(digits, "0") || 0), currency };
}

export function addMoney(a: Money, b: Money): Money {
  if (a.currency !== b.currency) throw new Error(`currency mismatch ${a.currency}/${b.currency}`);
  return { minor: a.minor + b.minor, currency: a.currency };
}
