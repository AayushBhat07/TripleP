import { test } from 'node:test';
import assert from 'node:assert/strict';
import { boqInsights, rupees } from '../lib/tenders/boq';

test('insights find the few items carrying most of the value, and arithmetic slips', () => {
  const insights = boqInsights([
    {
      title: 'Civil work',
      statedTotal: 1000,
      items: [
        { no: '1', description: 'Concrete', quantity: 10, unit: 'Cum', rate: 70, amount: 700, section: 'RCC' },
        { no: '2', description: 'Steel', quantity: 2, unit: 'Kg', rate: 100, amount: 250, section: 'RCC' },
        { no: '3', description: 'Paint', quantity: 1, unit: 'LS', rate: 50, amount: 50, section: 'Finishing' },
      ],
    },
  ]);
  assert.deepEqual(insights.majorItems.map(i => i.no), ['1', '2']);
  assert.deepEqual(insights.arithmetic.map(i => i.no), ['2']);
  assert.deepEqual(insights.lumpSum.map(i => i.no), ['3']);
  assert.deepEqual(insights.bySection[0], { section: 'RCC', amount: 950, items: 2 });
  assert.equal(rupees('Rs. 15,64,86,955/-'), 156486955);
});
