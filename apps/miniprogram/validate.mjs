import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import vm from 'node:vm';

export function validateConditionalBindings(markup, page = 'WXML') {
  const conditions = [];
  const source = markup.replace(/<!--[\s\S]*?-->/g, '');
  for (const [, , attributes] of source.matchAll(/<([A-Za-z][\w-]*)\b((?:"[^"]*"|'[^']*'|[^'">])*)>/g)) {
    for (const [, name, doubleQuoted, singleQuoted, unquoted] of attributes.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      if (name !== 'wx:if' && name !== 'wx:elif') continue;
      const value = doubleQuoted ?? singleQuoted;
      const binding = value?.trim().match(/^\{\{([\s\S]*?)\}\}$/);
      if (unquoted !== undefined || !binding?.[1].trim() || /\{\{|\}\}/.test(binding[1])) {
        throw new Error(`${page}: ${name} must contain one nonempty quoted {{ expression }} binding`);
      }
      conditions.push({ directive: name, expression: binding[1].trim() });
    }
  }
  return conditions;
}

// A bounded source/binding check, not the official WeChat WXML compiler.
export async function validateMiniProgram(output) {
  const config = JSON.parse(await readFile(join(output, 'app.json'), 'utf8'));
  const allowed = new Set(['view', 'text', 'button', 'input', 'textarea', 'picker', 'switch', 'scroll-view', 'checkbox-group', 'checkbox', 'label', 'block']);
  for (const page of config.pages) {
    const markup = await readFile(join(output, page + '.wxml'), 'utf8');
    validateConditionalBindings(markup, page);
    const source = await readFile(join(output, page + '.js'), 'utf8');
    let definition;
    vm.runInNewContext(source, { Page: value => { definition = value; }, getApp: () => ({ globalData: {} }), wx: {} }, { timeout: 1000 });
    if (!definition?.data) throw new Error(page + ': Page definition is missing');
    const tags = [...markup.matchAll(/<\/?([a-zA-Z][\w-]*)(?:\s[^>]*?)?\s*\/?\s*>/g)];
    const stack = [];
    for (const match of tags) {
      const [full, name] = match;
      if (!allowed.has(name)) throw new Error(`${page}: unsupported HTML or unknown component <${name}>`);
      if (full.startsWith('</')) { if (stack.pop() !== name) throw new Error(page + ': unbalanced WXML tags'); }
      else if (!full.endsWith('/>')) stack.push(name);
    }
    if (stack.length) throw new Error(page + ': unclosed WXML tags');
    for (const [, handler] of markup.matchAll(/\b(?:bind|catch):?[a-z]+="([A-Za-z_]\w*)"/g)) {
      if (typeof definition[handler] !== 'function') throw new Error(`${page}: missing event handler ${handler}`);
    }
  }
  console.log(`Checked ${config.pages.length} Mini Program pages, conditional expressions and event bindings`);
}
