import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clauseRegister } from '../components/analysis/clauses';

const metadata = (extra: object) => ({ tenderId: 't', tenderTitle: 't', generatedAt: new Date(), modelUsed: 'm', totalChunks: 1, processingTimeMs: 1, ...extra });

test('the clause register quotes what was found and lists the rest as not found', () => {
  const register = clauseRegister(
    metadata({
      keyTerms: [{ label: 'Performance guarantee', text: 'Performance Guarantee : 5% of tendered value.' }],
      riskClauses: [{ label: 'Final and binding', text: 'The decision of Engineer-in-Charge shall be final and binding.' }],
    })
  )!;
  assert.equal(register.find(c => c.label === 'Performance guarantee')?.quote, 'Performance Guarantee : 5% of tendered value.');
  assert.equal(register.find(c => c.label === 'Final and binding')?.group, 'Legal');
  assert.equal(register.find(c => c.label === 'Indemnity')?.quote, undefined);
  assert.equal(clauseRegister(metadata({})), null);
});
