import { useState } from "react";
import { apiRequest, ApiError } from "../api/client";
import { formatMoney, parseAmount } from "../utils/money";

interface Props {
  fromAccount: string;
  currencies: string[];
  onPaid: (entryId: string) => void;
}

export function PaymentForm({ fromAccount, currencies, onPaid }: Props) {
  const [payee, setPayee] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(currencies[0] ?? "AED");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const money = parseAmount(amount, currency);
    if (!payee || !money) {
      setError("Enter a payee and a valid amount.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await apiRequest<{ entry_id: string }>("/payments", {
        method: "POST",
        body: { from: fromAccount, payee, amount_minor: money.minor, currency, note },
        idempotencyKey,
      });
      onPaid(res.entry_id);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 400 ? "The payment was rejected." : "Payment failed, try again.");
    } finally {
      setBusy(false);
    }
  }

  const preview = parseAmount(amount, currency);
  return (
    <form onSubmit={submit} aria-busy={busy}>
      <label htmlFor="payee">Payee</label>
      <input id="payee" value={payee} onChange={(e) => setPayee(e.target.value)} />

      <label htmlFor="amount">Amount</label>
      <input id="amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      <select aria-label="Currency" value={currency} onChange={(e) => setCurrency(e.target.value)}>
        {currencies.map((c) => <option key={c}>{c}</option>)}
      </select>

      <label htmlFor="note">Note to payee</label>
      <input id="note" value={note} onChange={(e) => setNote(e.target.value)} />

      {preview && (
        <div className="receipt-preview"
             dangerouslySetInnerHTML={{ __html: `<b>${formatMoney(preview)}</b> to ${payee}<br/>${note}` }} />
      )}
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={busy}>Pay</button>
    </form>
  );
}
