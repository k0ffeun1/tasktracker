const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'server.py'), 'utf8')
  .split('<script>')[1].split('</script>')[0]
  .replace(/render\(\);\s*poll\(\);\s*setInterval\(poll, 1500\);\s*$/, '');

function setup() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', dataset: {}, addEventListener() {}, focus() {},
    });
    return elements.get(id);
  };
  const server = [{id: 1, title: 'Task', done: 0, urgent: 1, archived: 0}];
  const context = vm.createContext({
    AbortController, setTimeout, clearTimeout,
    localStorage: {getItem() {return null;}},
    document: {getElementById: element, addEventListener() {}, body: {dataset: {}}},
    fetch: async (url, options = {}) => {
      assert.equal(options.method, undefined);
      return response(server);
    },
  });
  vm.runInContext(source, context);
  vm.runInContext('render = () => {};', context);
  const run = code => vm.runInContext(code, context);
  run(`tasks = ${JSON.stringify(server)};`);
  return {context, server, run, element};
}

function response(data, status = 200) {
  return {ok: status === 200, status, json: async () => JSON.parse(JSON.stringify(data))};
}

test('checkbox changes only after the server confirms the write', async () => {
  const {context, run, server} = setup();
  let confirm;
  context.fetch = async (url, options = {}) => {
    if (!options.method) return response(server);
    return new Promise(resolve => {confirm = resolve;});
  };
  const save = run('toggleDone(tasks[0])');
  assert.equal(run('tasks[0].done'), 0);
  server[0].done = 1;
  confirm(response(server[0]));
  await save;
  assert.equal(run('tasks[0].done'), 1);
});

for (const status of [401, 503]) {
  test(`HTTP ${status} does not leave a false saved checkbox`, async () => {
    const {context, run, server, element} = setup();
    context.fetch = async (url, options = {}) => options.method
      ? response({}, status) : response(server);
    await run('toggleDone(tasks[0])');
    assert.equal(run('tasks[0].done'), 0);
    assert.equal(element('syncStatus').dataset.state, 'error');
    assert.equal(run('saving'), false);
  });
}

test('network failure preserves a new task draft and reports the failed save', async () => {
  const {context, run, element} = setup();
  element('addInput').value = 'Draft';
  context.fetch = async () => {throw new Error('offline');};
  await run('addTask()');
  assert.equal(element('addInput').value, 'Draft');
  assert.equal(run('tasks.length'), 1);
  assert.equal(element('syncStatus').dataset.state, 'error');
});

test('an old poll cannot undo a confirmed checkbox', async () => {
  const {context, run, server} = setup();
  let finishOldRead;
  let reads = 0;
  context.fetch = async (url, options = {}) => {
    if (options.method) {
      server[0].done = 1;
      return response(server[0]);
    }
    if (++reads === 1) return new Promise(resolve => {finishOldRead = resolve;});
    return response(server);
  };
  const oldPoll = run('poll()');
  await run('toggleDone(tasks[0])');
  finishOldRead(response([{...server[0], done: 0}]));
  await oldPoll;
  assert.equal(run('tasks[0].done'), 1);
});

test('repeated clicks during saving send a single write', async () => {
  const {context, run, server} = setup();
  let writes = 0;
  let confirm;
  context.fetch = async (url, options = {}) => {
    if (!options.method) return response(server);
    writes++;
    return new Promise(resolve => {confirm = resolve;});
  };
  const first = run('toggleDone(tasks[0])');
  await run('toggleDone(tasks[0])');
  assert.equal(writes, 1);
  server[0].done = 1;
  confirm(response(server[0]));
  await first;
});

test('slow polling keeps one request in flight and accepts its response', async () => {
  const {context, run, server} = setup();
  let finishFirst;
  let reads = 0;
  context.fetch = async () => {
    if (++reads === 1) return new Promise(resolve => {finishFirst = resolve;});
    return response([{...server[0], done: 1}]);
  };
  const first = run('poll()');
  await run('poll()');
  await run('poll()');
  assert.equal(reads, 1, 'Repeated timer ticks must not invalidate or overlap a slow request');
  finishFirst(response(server));
  await first;
  await new Promise(setImmediate);
  assert.equal(reads, 2, 'Timer ticks coalesce into one follow-up request');
  assert.equal(run('tasks[0].done'), 1);
});

test('a stalled read times out visibly and releases the polling lock', async () => {
  const {context, run, element} = setup();
  let timeout;
  context.setTimeout = fn => {timeout = fn; return 1;};
  context.clearTimeout = () => {};
  context.fetch = (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('timeout')));
  });
  const read = run('poll()');
  timeout();
  await read;
  assert.equal(element('syncStatus').dataset.state, 'error');
  assert.equal(run('polling'), false);
});
