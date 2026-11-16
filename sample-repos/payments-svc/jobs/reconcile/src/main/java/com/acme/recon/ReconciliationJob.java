package com.acme.recon;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Nightly reconciliation: matches the card processor's settlement file against ledger
 * postings on the card-clearing account and reports breaks.
 */
public class ReconciliationJob {

    private final SettlementFileReader reader;
    private final LedgerClient ledger;
    private final BreakReporter reporter;

    public ReconciliationJob(SettlementFileReader reader, LedgerClient ledger, BreakReporter reporter) {
        this.reader = reader;
        this.ledger = ledger;
        this.reporter = reporter;
    }

    /** Runs reconciliation for one settlement date and returns the number of breaks. */
    public int run(LocalDate settlementDate) {
        List<SettlementLine> lines = reader.read(settlementDate);
        Map<String, BigDecimal> ledgerByReference = ledger.clearingPostings(settlementDate);
        List<Break> breaks = new ArrayList<>();

        Map<String, BigDecimal> settledByReference = new HashMap<>();
        for (SettlementLine line : lines) {
            settledByReference.merge(line.reference(), line.amount(), BigDecimal::add);
        }
        for (Map.Entry<String, BigDecimal> settled : settledByReference.entrySet()) {
            BigDecimal booked = ledgerByReference.get(settled.getKey());
            if (booked == null) {
                breaks.add(Break.missingInLedger(settled.getKey(), settled.getValue()));
            } else if (booked.compareTo(settled.getValue()) != 0) {
                breaks.add(Break.amountMismatch(settled.getKey(), booked, settled.getValue()));
            }
        }
        for (String reference : ledgerByReference.keySet()) {
            if (!settledByReference.containsKey(reference)) {
                breaks.add(Break.missingInSettlement(reference, ledgerByReference.get(reference)));
            }
        }
        reporter.report(settlementDate, breaks);
        return breaks.size();
    }
}
