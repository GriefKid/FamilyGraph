import {readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const file = join(root, 'www', 'index.html');
const serverUrl = String(process.env.FAMILYGRAPH_SERVER_URL || '').trim().replace(/\/$/, '');

if (!serverUrl || !/^https:\/\//i.test(serverUrl)) {
  throw new Error('Set FAMILYGRAPH_SERVER_URL to the HTTPS URL of the deployed FamilyGraph server.');
}

const html = await readFile(file, 'utf8');
await writeFile(file, html.replaceAll('__FAMILYGRAPH_SERVER_URL__', serverUrl), 'utf8');
console.log(`Mobile shell configured for ${serverUrl}`);
