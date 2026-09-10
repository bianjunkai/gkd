import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';
import { validateConditionalBindings } from './validate.mjs';

const source = new URL('./src/', import.meta.url);
const config = JSON.parse(await readFile(new URL('app.json', source), 'utf8'));
const pages = await Promise.all(config.pages.map(async page => ({
  page, markup: await readFile(new URL(page + '.wxml', source), 'utf8'),
})));

describe('WXML conditional binding gate (not a WeChat renderer)', () => {
  it.each([
    'wx:if="proposal"', 'wx:elif="error"', 'wx:if="!captures.length &amp;&amp; !busy"',
    'wx:if=""', 'wx:if="{{ }}"', 'wx:if', 'wx:if={{busy}}', 'wx:if="{{busy}} trailing"',
    'wx:if="{{busy}}{{error}}"',
  ])('rejects a literal or malformed condition: %s', attribute => {
    expect(() => validateConditionalBindings(`<view ${attribute}/>`)).toThrow('nonempty quoted {{ expression }}');
  });

  it('accepts single/double quotes, spacing and ignores comments/non-condition attributes', () => {
    expect(validateConditionalBindings('<!-- <view wx:if="broken"/> --><view data-text="wx:if=literal" wx:if = "{{ busy }}"><text wx:elif=\'{{ !error }}\'/></view>'))
      .toEqual([{ directive: 'wx:if', expression: 'busy' }, { directive: 'wx:elif', expression: '!error' }]);
  });

  it.each(pages)('checks every real conditional in $page', ({ page, markup }) => {
    const conditions = validateConditionalBindings(markup, page);
    expect(conditions.length).toBe((markup.match(/\bwx:(?:if|elif)\s*=/g) || []).length);
  });

  it.each([
    ['pages/proposal/index', 'proposal', { proposal: null }, { proposal: { status: 'pending_confirmation' } }],
    ['pages/proposal/index', "proposal.status === 'stale'", { proposal: { status: 'pending_confirmation' } }, { proposal: { status: 'stale' } }],
    ['pages/proposal/index', '!readonly', { readonly: true }, { readonly: false }],
    ['pages/inbox/index', '!captures.length &amp;&amp; !busy &amp;&amp; !error', { captures: [], busy: true, error: '' }, { captures: [], busy: false, error: '' }],
    ['pages/inbox/index', 'item.integrity_error', { item: {} }, { item: { integrity_error: { code: 'CAPTURE_UNAVAILABLE' } } }],
    ['pages/settings/index', '!groups.length &amp;&amp; !captures.length', { groups: [{}], captures: [] }, { groups: [], captures: [] }],
  ])('has distinct false/true states for %s: %s', (page, expression, falseState, trueState) => {
    const bindings = validateConditionalBindings(pages.find(p => p.page === page).markup, page);
    expect(bindings.some(condition => condition.expression === expression)).toBe(true);
    const decoded = expression.replaceAll('&amp;', '&').replaceAll('&gt;', '>').replaceAll('&lt;', '<');
    expect(vm.runInNewContext(`Boolean(${decoded})`, falseState, { timeout: 100 })).toBe(false);
    expect(vm.runInNewContext(`Boolean(${decoded})`, trueState, { timeout: 100 })).toBe(true);
  });
});
