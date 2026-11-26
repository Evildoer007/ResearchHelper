const assert = require('node:assert/strict');
const {createHash, webcrypto} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../products/app/frontend/ui/artifact-export.js'), 'utf8');
const window = {};
vm.runInNewContext(source, {window, crypto: webcrypto, TextEncoder, Uint8Array, atob});
const {recoveryEntries, recoveryBytes} = window.RHArtifactExport;
const hash = value => createHash('sha256').update(value).digest('hex');
const original = Buffer.from([239, 187, 191, 255, 0]);
const html = '<html><body>历史报告</body></html>';
const task = {id: 'rh_source', version: 2, question: '研究问题'};
const report = {version: 1, html, html_sha256: hash(html),
  content: {task_id: task.id, research_version: 1, title: '历史研究'}};
const material = {task_id: task.id, research_version: 1, name: '../CON.txt',
  original_base64: original.toString('base64'), sha256: hash(original), byte_count: original.length};
const backup = {workspaceVersion: 4, tasks: [task], reports: [report], originals: [material]};

test('recovery takes saved report and original bytes from the matching task and version', async () => {
  const {reports, originals} = recoveryEntries({
    ...backup,
    reports: [...backup.reports, {...report, version: 3}, {...report, content: {...report.content, task_id: 'another'}}],
    originals: [...backup.originals, {...material, name: 'bad.exe'}, {...material, research_version: 3},
      {...material, task_id: 'another'}],
  });
  assert.equal(reports.length, 1);
  assert.equal(originals.length, 1);
  assert.equal(reports[0].filename, '历史研究_v1.html');
  assert.ok(!/[\\/]/.test(originals[0].filename));
  assert.deepEqual(Buffer.from(await recoveryBytes(reports[0])), Buffer.from(html));
  assert.deepEqual(Buffer.from(await recoveryBytes(originals[0])), original);
});

test('original validation checks decoded bytes and accepts a retained empty file', async () => {
  const {reports, originals} = recoveryEntries(backup);
  await assert.rejects(recoveryBytes({...reports[0], html: 'changed'}), /指纹/);
  await assert.rejects(recoveryBytes({...originals[0], content_base64: '!!!!'}), /编码/);
  await assert.rejects(recoveryBytes({...originals[0], byteCount: 1}), /长度/);
  await assert.rejects(recoveryBytes({...originals[0], sha256: hash('wrong')}), /指纹/);
  const empty = {...originals[0], content_base64: '', byteCount: 0, sha256: hash('')};
  assert.equal((await recoveryBytes(empty)).length, 0);
});

test('previous task backups preserve standard, detailed and embedded material files', async () => {
  const source = {name: '历史资料.md', hash: hash(original), original_base64: original.toString('base64')};
  const old = {...task, report_html: html, report_detailed_html: html + '\\n',
    materials: [{task_id: task.id, research_version: 1, kind: 'material', snapshot: source},
      {task_id: task.id, research_version: 2, retained_from_version: 1, kind: 'material', snapshot: source}],
    source_records: [{task_id: task.id, research_version: 1, kind: 'filing', snapshot: {material: source}}]};
  const {reports, originals} = recoveryEntries({workspaceVersion: 4, tasks: [old]});
  assert.equal(reports.length, 2);
  assert.match(reports[0].edition, /标准报告/);
  assert.match(reports[1].edition, /详细报告/);
  assert.equal(originals.length, 1);
  assert.equal(originals[0].filename, source.name);
  assert.deepEqual(Buffer.from(await recoveryBytes(originals[0])), original);
  const emptyIndex = recoveryEntries({workspaceVersion: 4, tasks: [old], originals: []});
  assert.equal(emptyIndex.originals.length, 1);
  assert.deepEqual(Buffer.from(await recoveryBytes(emptyIndex.originals[0])), original);
  const partialIndex = recoveryEntries({workspaceVersion: 4, tasks: [old], originals: [
    material, {...material, name: source.name},
  ]});
  assert.equal(partialIndex.originals.length, 2);
  const legacy = recoveryEntries({title: '未保存文件的旧备份'});
  assert.equal(legacy.reports.length + legacy.originals.length, 0);
});
