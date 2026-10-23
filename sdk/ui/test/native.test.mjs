import { test } from 'node:test';
import assert from 'node:assert/strict';
import { defineHome, defineComponent } from '../dist/index.js';
test('developer components produce a native data-only Home', () => {
 const Heading = defineComponent(({ title }) => ({ id: 'heading', type: 'heading', text: title }));
 const home = defineHome({ title: 'Home', renderer: 'native', content: Heading({ title: 'Custom title' }) });
 assert.equal(JSON.parse(JSON.stringify(home)).content.text, 'Custom title');
 assert.ok(!JSON.stringify(home).includes('iframe'));
});
test('native authoring rejects iframe types and conflicting component identities', () => {
 assert.throws(() => defineHome({ title: 'Home', renderer: 'native', content: { id: 'one', type: 'iframe' } }));
 assert.throws(() => defineHome({ title: 'Home', renderer: 'native', content: { id: 'root', type: 'stack', children: [{id:'one',type:'text'},{id:'one',type:'text'}] } }));
});
