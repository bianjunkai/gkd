// Optional, network-free recovery of public npm artifacts from an existing cache.
// Never changes the source cache. It creates metadata only for verified archives.
import { createRequire } from 'node:module';
import { dirname, resolve, join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { mkdir } from 'node:fs/promises';

const [sourceArg, targetArg = '.cache/npm'] = process.argv.slice(2);
if (!sourceArg) throw new Error('Usage: node scripts/prepare-offline-cache.mjs <npm-cache> [output-cache]');
const source = resolve(sourceArg, '_cacache');
const target = resolve(targetArg, '_cacache');
if (source.toLowerCase() === target.toLowerCase()) throw new Error('Source and output must differ');
const require = createRequire(import.meta.url);
const npmModules = join(dirname(process.execPath), 'node_modules/npm/node_modules');
const cache = require(join(npmModules, 'cacache'));
const semver = require(join(npmModules, 'semver'));
await mkdir(target, { recursive: true });

function manifestFromTar(data) {
  const tar = gunzipSync(data, { maxOutputLength: 256 * 1024 * 1024 });
  for (let offset = 0; offset + 512 <= tar.length;) {
    const header = tar.subarray(offset, offset + 512);
    const name = header.toString('utf8', 0, 100).replace(/\0.*$/s, '');
    const length = parseInt(header.toString('ascii', 124, 136).replace(/\0.*$/s, '').trim(), 8) || 0;
    if (/^[^/]+\/package\.json$/.test(name)) {
      return JSON.parse(tar.toString('utf8', offset + 512, offset + 512 + length));
    }
    offset += 512 + Math.ceil(length / 512) * 512;
  }
  throw new Error('Archive has no package.json');
}

function metadata(url, size, type) {
  return {
    time: Date.now(), url, reqHeaders: type === 'application/json' ? { accept: 'application/json' } : {},
    resHeaders: { 'content-type': type, 'cache-control': 'public, max-age=31536000',
      date: new Date().toUTCString(), 'content-length': String(size) },
    options: { compress: true },
  };
}

const packages = new Map();
let archives = 0;
for (const entry of Object.values(await cache.ls(source))) {
  if (!entry.key.startsWith('make-fetch-happen:request-cache:') || !entry.key.endsWith('.tgz')) continue;
  let data, pkg;
  try {
    ({ data } = await cache.get(source, entry.key));
    pkg = manifestFromTar(data);
  } catch { continue; } // A stale cache entry is not a usable artifact.
  if (!semver.valid(pkg.version) || !pkg.name) continue;
  const shortName = pkg.name.split('/').at(-1);
  const url = `https://registry.npmjs.org/${pkg.name}/-/${shortName}-${pkg.version}.tgz`;
  const integrity = await cache.put(target, `make-fetch-happen:request-cache:${url}`, data, {
    metadata: metadata(url, data.length, 'application/octet-stream'),
  });
  const packument = packages.get(pkg.name) || { name: pkg.name, versions: {}, 'dist-tags': {} };
  packument.versions[pkg.version] = { ...pkg, dist: { tarball: url, integrity: String(integrity) } };
  packages.set(pkg.name, packument);
  archives++;
}
for (const [name, packument] of packages) {
  packument['dist-tags'].latest = Object.keys(packument.versions).sort(semver.rcompare)[0];
  const url = `https://registry.npmjs.org/${name.replace('/', '%2f')}`;
  const data = Buffer.from(JSON.stringify(packument));
  await cache.put(target, `make-fetch-happen:request-cache:${url}`, data, {
    metadata: metadata(url, data.length, 'application/json'),
  });
}
console.log(`Prepared ${archives} verified archives for ${packages.size} packages in ${target}`);
