import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const apiRoot = resolve(root, 'services/api');
const python = process.env.GKD_PYTHON || resolve(apiRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const [mode = 'serve', ...extra] = process.argv.slice(2);
if (!existsSync(python)) {
  console.error('未找到项目 Python 环境，请先按 README 安装依赖，或设置 GKD_PYTHON 为解释器的完整路径。');
  process.exit(1);
}
let args;
if (mode === 'test') {
  const cache = resolve(root, '.cache');
  mkdirSync(cache, { recursive: true });
  args = ['-X', 'utf8', '-m', 'pytest', '-q', '--tb=short', '--basetemp', resolve(cache, `pytest-${randomUUID()}`), ...extra];
} else if (mode === 'backup') {
  const pathFlags = new Set(['--output', '--archive', '--destination']);
  args = ['-X', 'utf8', '-m', 'app.backup', ...extra.map((value, index) => pathFlags.has(extra[index - 1]) ? resolve(root, value) : value)];
} else if (['start', 'serve', 'dev'].includes(mode)) {
  if (mode === 'start' && !existsSync(resolve(root, 'apps/web/dist/index.html'))) {
    console.error('Web 工作台尚未构建，请先运行 npm run build。');
    process.exit(1);
  }
  args = ['-X', 'utf8', '-m', 'app.cli', ...(mode === 'dev' ? ['--reload'] : []), ...extra];
} else {
  console.error(`未知命令：${mode}`);
  process.exit(1);
}
const child = spawn(python, args, { cwd: apiRoot, stdio: 'inherit', windowsHide: true });
child.on('error', error => { console.error(`无法启动 Python：${error.message}`); process.exitCode = 1; });
child.on('exit', (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); });
