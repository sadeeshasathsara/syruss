import test from "node:test";
import assert from "node:assert/strict";
import { ASKS } from "../src/lib/samples.ts";

test("the sample answers add up to the rows shown", () => {
  const num = (s: string) => Number(s.replace(/,/g, ""));
  const receipts = ASKS[0].rows.reduce((n, r) => n + num(r.cells[2]), 0);
  assert.match(ASKS[0].answer, new RegExp(receipts.toFixed(2)));
  const invoices = ASKS[1].rows.reduce((n, r) => n + num(r.cells[2]), 0);
  assert.match(ASKS[1].answer, /2,180\.00/);
  assert.equal(invoices, 2180);
});
