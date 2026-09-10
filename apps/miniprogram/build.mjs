import { build } from 'esbuild';
import { mkdir, readdir, copyFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { validateMiniProgram } from './validate.mjs';

const root = import.meta.dirname;
const source = join(root, 'src');
const output = join(root, 'dist');
const entryPoints = [];
async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await collect(path);
    else if (entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts') && (entry.name === 'app.ts' || path.includes(join('pages', '')))) entryPoints.push(path);
    else if (/\.(json|wxml|wxss)$/.test(entry.name)) {
      const target = join(output, path.slice(source.length + 1));
      await mkdir(dirname(target), { recursive: true }); await copyFile(path, target);
    }
  }
}
const baseURL = (process.env.GKD_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const parsed = new URL(baseURL);
if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') throw new Error('GKD_API_BASE_URL must be an HTTP(S) origin without credentials or a path');
if (process.env.NODE_ENV === 'production' && parsed.protocol !== 'https:') throw new Error('Production Mini Program requires HTTPS');
await collect(source);
await build({ entryPoints, outdir: output, outbase: source, bundle: true, platform: 'neutral', format: 'cjs', target: 'es2019',
  define: { __GKD_API_BASE_URL__: JSON.stringify(baseURL) }, sourcemap: false, minify: false, logLevel: 'info' });
console.log('Mini Program output:', resolve(output));
await validateMiniProgram(output);
