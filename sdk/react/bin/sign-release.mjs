/** Usage: node sign-release.mjs release.json private-key.pem artifact.mjs
 * release.json: {publisher, package, component}; never reads application secrets.
 */
import fs from 'node:fs/promises';
import {createHash, sign} from 'node:crypto';
const [releaseFile, keyFile, artifact] = process.argv.slice(2);
if (!artifact) throw new Error('Expected release.json private-key.pem artifact.mjs');
const release = JSON.parse(await fs.readFile(releaseFile, 'utf8'));
const bytes = await fs.readFile(artifact);
if (createHash('sha256').update(bytes).digest('hex') !== release.component.digest) throw new Error('Artifact digest mismatch.');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const payload = JSON.stringify(canonical({format: 'integral.ui.v1', ...release})).replace(/[\u007f-\uffff]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
const signature = sign(null, Buffer.from(payload), await fs.readFile(keyFile)).toString('base64');
await fs.writeFile(artifact.replace(/\.mjs$/, `.${release.component.key}.sig`), signature + '\n', {mode: 0o600});
console.log('UI release signature written. No release was published or activated.');
