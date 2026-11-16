import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { Money } from "../utils/money";

export interface AccountBalance {
  accountId: string;
  name: string;
  balance: Money;
}

export function useBalances(refreshMs = 30_000) {
  const [balances, setBalances] = useState<AccountBalance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    async function load() {
      try {
        const data = await apiRequest<AccountBalance[]>("/accounts/balances", { signal: controller.signal });
        setBalances(data);
        setError(null);
      } catch (err) {
        if (!controller.signal.aborted) setError(err as Error);
      } finally {
        setLoading(false);
        timer = setTimeout(load, refreshMs);
      }
    }
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refreshMs]);

  return { balances, loading, error };
}
