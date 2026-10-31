// Sample questions and answers for the landing page's "Ask Syruss" demo. They are made up: no real business or person.

export type AskExample = {
  question: string;
  /** What Syruss does on the way to the answer, one short line each. */
  thinking: string[];
  answer: string;
  rows: { cells: string[] }[];
  head: string[];
};

export const ASKS: AskExample[] = [
  {
    question: "What did we spend with Harbour Foods in March?",
    thinking: ["Looking for receipts from Harbour Foods", "Found 3 dated in March", "Adding up their totals"],
    answer: "You spent 561.70 with Harbour Foods in March, across 3 receipts.",
    head: ["Date", "Document", "Total"],
    rows: [
      { cells: ["3 Mar", "receipt-0303", "212.10"] },
      { cells: ["14 Mar", "receipt-0314", "48.60"] },
      { cells: ["27 Mar", "receipt-0327", "301.00"] },
    ],
  },
  {
    question: "Show invoices that are due this week",
    thinking: ["Looking for invoices with a due date this week", "Found 2 that are still unpaid", "Adding up what is owed"],
    answer: "Two invoices are due this week, 2,180.00 in all.",
    head: ["Supplier", "Due", "Total"],
    rows: [
      { cells: ["Coldline Storage", "10 Apr", "1,420.00"] },
      { cells: ["Marlow Packaging", "12 Apr", "760.00"] },
    ],
  },
  {
    question: "Which delivery notes still need a signature?",
    thinking: ["Checking every delivery note for a signature", "Found 1 where it is unclear", "Listing it for you"],
    answer: "One delivery note is waiting for a signature.",
    head: ["Supplier", "Delivered", "Items"],
    rows: [{ cells: ["Northline Freight", "9 Apr", "12 cartons"] }],
  },
];
