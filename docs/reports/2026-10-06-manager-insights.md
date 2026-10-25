# Management Insights report - 2026-10-06

Scope: Abraj profile, TDC and HDEC, June through September 2026. Payment and quotation statuses are current as of the 6 October snapshot.

Outputs under `D:/PO TOOL/REPORTS/2026/MANAGEMENT/`:

- `Management Insights - TDC and HDEC - June to September 2026.pdf`: 9-page management report.
- Same filename with `.html`: portable editable report, note editing/save/print controls and full searchable document register.
- Same filename with ` - data.json`: extracted Insights figures for reproducibility.
- `source/`: report builder and preserved input snapshot for follow-up adjustments.

The report contains an executive overview, monthly comparison, account reviews with top items, payment and cost follow-ups, and a complete invoice register. The HTML includes the complete quotation and delivery-note register too. It contains no external assets or services. Actual invoice copies are not appended to this management brief.

Figures: charged $145,460.49; recorded costs $98,454.49; recorded direct profit $47,006.00; paid $96,272.89; unpaid $49,187.60. Four TDC invoices have incomplete costs, prominently disclosed. All 12 HDEC invoices are fully costed.

Verification output:

- PASS TDC: 51 invoices, 43 paid, 8 unpaid; four monthly totals reconciled.
- PASS HDEC: 12 invoices, 2 paid, 10 unpaid; four monthly totals reconciled.
- PASS HTML: 191 rows; HDEC filter 96 rows; edit/save notes; desktop and mobile layout; no script errors; all nine print sections clear of their footers.
- PASS PDF: 9 pages; all 63 invoices; correct page starts and footer numbers; no carried rows or overlaps; every page rendered and visually inspected.
- PDF SHA256: `925fb31f65ebf2bc7249a389b5a3aec09d51e90db44722851211decbb15fbef3`.

The existing Insights helpers ran on coherent document and shared-library SQLite snapshots. No application code or live database was changed. The live test lock remained with Claude.
