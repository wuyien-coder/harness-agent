/**
 * 前端 Markdown 渲染回归测试（Node 直接运行，无需浏览器）。
 *   node tests_harness/test_markdown_render.js
 *
 * 守护：大模型回答里 “1. 标题 + 缩进说明” 这类结构，曾被渲染器切成多个 <ol>，
 * 每段都从 1 重新编号。修复后应保持单一列表、序号连续、HTML 标签闭合。
 */
const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'harness', 'panel', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf-8');
function grab(name) {
  const re = new RegExp('(function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\})');
  const m = html.match(re);
  if (!m) throw new Error('index.html 未找到函数 ' + name);
  return m[1];
}
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
eval(grab('mdInline'));
eval(grab('renderMarkdown'));

let failed = 0;
function assert(cond, msg) {
  if (cond) { console.log('  [OK] ' + msg); }
  else { console.error('  [FAIL] ' + msg); failed++; }
}

// 1) 缩进说明段落不得割裂有序列表（核心回归）
const nested = '1. **标题一**\n   说明文字一\n2. **标题二**\n   说明文字二\n3. **标题三**\n   说明文字三';
const r1 = renderMarkdown(nested);
assert((r1.match(/<ol/g) || []).length === 1, '缩进说明场景只生成一个 <ol>');
assert([...r1.matchAll(/<li value="(\d+)"/g)].map(m => m[1]).join(',') === '1,2,3', '序号连续为 1,2,3');

// 2) HTML 标签必须闭合且无重复闭合
const cases = {
  '连续无空行': '1. 第一点\n2. 第二点\n3. 第三点',
  '空行分隔': '1. 第一点\n\n2. 第二点\n\n3. 第三点',
  '列表接标题': '1. 第一\n2. 第二\n## 小结\n正文段落',
  'ul 后 ol': '- 甲\n- 乙\n1. 一\n2. 二',
  '列表接段落': '1. 第一\n2. 第二\n这是普通段落。',
};
for (const [name, text] of Object.entries(cases)) {
  const r = renderMarkdown(text);
  const liO = (r.match(/<li[ >]/g) || []).length, liC = (r.match(/<\/li>/g) || []).length;
  const olO = (r.match(/<ol[ >]/g) || []).length, olC = (r.match(/<\/ol>/g) || []).length;
  assert(liO === liC && olO === olC && !/<\/li>\s*<\/li>/.test(r), name + '：标签闭合正确');
}

// 3) 显式起始序号被保留
const r3 = renderMarkdown('3. 第三\n4. 第四');
assert(/<ol start="3">/.test(r3) && /<li value="4">/.test(r3), '显式起始序号 3/4 被保留');

if (failed) { console.error(`\n${failed} 项失败`); process.exit(1); }
console.log('\n全部通过：Markdown 序号渲染回归测试');
