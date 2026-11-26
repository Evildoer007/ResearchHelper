/* Report preview reads the selected saved edition and owns its recovery UI. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const reportPath = '/api/tasks/saved/report/1?profile=standard';
const digest = 'a'.repeat(64);

function harness({ base = 'http://127.0.0.1:8080/' } = {}) {
  const timers = new Map(), windowListeners = new Map();
  let clock = 0, currentFrame = null, hasPanel = true, authenticationFailures = 0;
  const documentMock = { baseURI: base, activeElement: null, body: {}, createElement: () => ({ content: panel }) };
  const title = { textContent: '' }, message = { textContent: '' };
  const retry = { focus() { documentMock.activeElement = retry; } };
  const print = { disabled: false };
  let retryHidden = true;
  Object.defineProperty(retry, 'hidden', {
    get: () => retryHidden,
    set: value => {
      retryHidden = value;
      if (value && documentMock.activeElement === retry) documentMock.activeElement = documentMock.body;
    }
  });
  const panel = {
    hidden: true, dataset: {}, contains: element => [retry,title,message].includes(element),
    querySelector: selector => selector.includes('title') ? title : selector.includes('message') ? message : retry
  };
  const pane = {
    querySelector: selector => selector === '#report-frame' ? currentFrame : hasPanel ? panel : null,
    querySelectorAll: () => [print]
  };
  const windowMock = {
    location: new URL(base),
    addEventListener: (type, callback) => windowListeners.set(type, callback),
    removeEventListener: type => windowListeners.delete(type)
  };
  const box = {
    window: windowMock, document: documentMock, URL,
    setTimeout: callback => { timers.set(++clock, callback); return clock; },
    clearTimeout: id => timers.delete(id)
  };
  vm.runInNewContext(fs.readFileSync('products/app/frontend/ui/desk-report.js','utf8'), box);
  const reader = box.window.RHReportReader.create(pane, { onAuthenticationRequired: () => authenticationFailures++ });
  const doc = ({ paper = {}, ...options } = {}) => ({
    URL: new URL(reportPath, base).href, readyState: 'complete', contentType: 'text/html',
    title: '研究报告', body: { textContent: '完整研究报告', dataset: {reportVariant:'standard'} },
    querySelector: selector => selector.startsWith('script#rh-publication-document')
      ? {textContent: JSON.stringify({schema:'researchhelper.presentation-document',version:1,
         profile:'standard',content_hash:digest,...paper})}
      : ['main','h1'].includes(selector) ? {} : null,
    ...options
  });

  function frame(attrs = { src: reportPath, 'data-report-digest': digest }) {
    const listeners = new Map(), messages = [];
    const value = {
      attrs: { ...attrs }, doc: doc({ URL: 'about:blank', body: { textContent: '' } }),
      isConnected: true, hidden: false, prints: 0, listeners, messages,
      focus() { documentMock.activeElement = value; },
      get contentDocument() { return value.doc; },
      contentWindow: {
        print() { value.prints++; },
        postMessage(payload) { messages.push(payload); }
      },
      hasAttribute: name => Object.hasOwn(value.attrs,name),
      getAttribute: name => value.attrs[name] ?? null,
      setAttribute: (name, field) => { value.attrs[name] = field; },
      addEventListener: (name, callback) => listeners.set(name, callback),
      removeEventListener: name => listeners.delete(name),
      before: () => { hasPanel = true; },
      cloneNode: () => frame(value.attrs),
      replaceWith(next) { value.isConnected = false; currentFrame = next; },
      emit: name => listeners.get(name)?.({ currentTarget: value })
    };
    return value;
  }

  return {
    reader, panel, title, message, retry, print, doc, frame, timers, windowListeners, document: documentMock,
    get current() { return currentFrame; },
    get authenticationFailures() { return authenticationFailures; },
    set(value) { currentFrame = value; reader.updated(); },
    withoutPanel() { hasPanel = false; },
    messageFrom(source, data, origin = 'null') { windowListeners.get('message')?.({ source, data, origin }); }
  };
}

test('an initial blank frame remains loading and printing is disabled', () => {
  const h = harness(), frame = h.frame();
  h.set(frame);
  assert.equal(h.panel.dataset.state, 'loading');
  assert.equal(h.retry.hidden, true);
  assert.equal(h.print.disabled, true);
  assert.equal(h.reader.isReady(frame), false);
  h.reader.destroy();
});

test('a refreshed toolbar uses the readiness of the retained report frame', () => {
  const h = harness(), frame = h.frame();
  h.set(frame);
  h.print.disabled=false;
  h.reader.updated();
  assert.equal(h.print.disabled,true);
  frame.doc=h.doc();frame.emit('load');
  h.print.disabled=true;
  h.reader.updated();
  assert.equal(h.print.disabled,false);
  assert.equal(h.reader.isReady(frame),true);
  h.reader.destroy();
});

test('a failed response retries exactly the saved URL in a fresh frame', () => {
  const h = harness(), frame = h.frame();
  h.set(frame);
  frame.doc = h.doc({ contentType:'application/json', body:{textContent:'{"error":"private reason"}'} });
  frame.emit('load');
  assert.equal(h.panel.dataset.state,'error');
  assert.equal(frame.hidden,true);
  assert.equal(h.retry.hidden,false);
  assert.doesNotMatch(h.message.textContent,/private/);
  h.retry.focus();
  h.reader.retry();
  const fresh = h.current;
  assert.equal(frame.isConnected,false);
  assert.notEqual(fresh,frame);
  assert.equal(fresh.attrs.src,reportPath);
  assert.equal(fresh.attrs['data-report-digest'],digest);
  assert.equal(h.document.activeElement,h.document.body);
  fresh.doc = h.doc();
  fresh.emit('load');
  assert.equal(h.reader.isReady(fresh),true);
  assert.equal(h.document.activeElement,fresh);
  assert.equal(h.print.disabled,false);
  h.reader.destroy();
});

test('an inline report keeps its exact HTML across a read error', () => {
  const h = harness(), text = '<h1>公开完整内容</h1>', frame = h.frame({ srcdoc:text });
  h.set(frame);
  frame.emit('error');
  h.reader.retry();
  const fresh = h.current;
  assert.equal(fresh.attrs.srcdoc,text);
  fresh.doc = h.doc({ URL:'about:srcdoc', querySelector:() => null });
  fresh.emit('load');
  assert.equal(h.reader.isReady(fresh),true);
  h.reader.destroy();
});

test('a late response from another frame leaves the selected task in place', () => {
  const h = harness(), old = h.frame(), fresh = h.frame({
    src:reportPath.replace('/saved/','/other/'), 'data-report-digest':digest
  });
  h.set(old);
  const late = old.listeners.get('load');
  h.set(fresh);
  late({currentTarget:old});
  assert.equal(h.panel.dataset.state,'loading');
  fresh.doc = h.doc({ URL:'http://127.0.0.1:8080'+fresh.attrs.src });
  fresh.emit('load');
  assert.equal(h.reader.isReady(fresh),true);
  h.reader.destroy();
});

test('a blocked HTTP frame, a redirect and a non-report HTML page offer a retry', () => {
  for (const bad of [
    () => null,
    h => h.doc({ URL:'http://127.0.0.1:8080/login/index.html' }),
    h => h.doc({ querySelector:() => null }),
    h => h.doc({ body:{textContent:''} })
  ]) {
    const h = harness(), frame = h.frame();
    h.set(frame);
    frame.doc = bad(h);
    frame.emit('load');
    assert.equal(h.panel.dataset.state,'error');
    assert.equal(h.print.disabled,true);
    assert.equal(h.retry.hidden,false);
    h.reader.destroy();
  }
});

test('a report at the exact URL must carry the selected edition and profile', () => {
  for (const paper of [{content_hash:'b'.repeat(64)}, {profile:'detailed'}, {version:2}, {schema:'unrelated'}]) {
    const h = harness(), frame = h.frame();
    h.set(frame);
    frame.doc = h.doc({paper});
    frame.emit('load');
    assert.equal(h.panel.dataset.state,'error');
    assert.equal(frame.hidden,true);
    assert.equal(h.print.disabled,true);
    h.reader.destroy();
  }
});

test('an authenticated preview response can return to login without a write', () => {
  const h = harness(), frame = h.frame();
  h.set(frame);
  frame.doc = h.doc({ contentType:'application/json', body:{textContent:'{"error":"local","failure_code":"authentication_required"}'} });
  frame.emit('load');
  assert.equal(h.authenticationFailures,1);
  assert.equal(h.reader.isReady(frame),false);
  h.reader.destroy();
});

test('an opaque file preview requires confirmation from the expected historical report', () => {
  const h = harness({base:'file:///temporary/demo/public/index.html'});
  const frame = h.frame({src:'reports/broker.html'});
  h.set(frame);
  frame.doc = null;
  frame.emit('load');
  assert.equal(h.reader.isReady(frame),false);
  assert.equal(frame.messages[0].type,'rh:check-local-report');
  const data = {type:'rh:local-report-ready',key:'broker',href:'file:///temporary/demo/public/reports/broker.html'};
  h.messageFrom({},data);
  h.messageFrom(frame.contentWindow,{...data,href:'file:///temporary/demo/public/reports/semi.html'});
  h.messageFrom(frame.contentWindow,data,'https://invalid.example');
  assert.equal(h.reader.isReady(frame),false);
  h.messageFrom(frame.contentWindow,data);
  assert.equal(h.reader.isReady(frame),true);
  h.reader.print(frame);
  assert.equal(frame.messages[1].type,'rh:print-local-report');
  h.reader.destroy();
  assert.equal(h.windowListeners.size,0);
});

test('historical previews without a state panel receive the same retry controls', () => {
  const h = harness(), frame = h.frame();
  h.withoutPanel();
  h.set(frame);
  frame.emit('error');
  assert.equal(h.panel.dataset.state,'error');
  assert.equal(h.retry.hidden,false);
  h.reader.destroy();
});

test('timeout closes the attempt and a late completion cannot enable printing', () => {
  const h = harness(), frame = h.frame();
  h.set(frame);
  [...h.timers.values()][0]();
  frame.doc = h.doc();
  frame.emit('load');
  assert.equal(h.panel.dataset.state,'error');
  assert.equal(h.reader.isReady(frame),false);
  h.reader.destroy();
  assert.equal(h.timers.size,0);
  assert.equal(frame.listeners.size,0);
  h.reader.retry();
  assert.equal(h.current,frame);
});

test('retry while loading and retry without a report are no-ops', () => {
  const h = harness(), frame = h.frame();
  h.reader.updated();
  h.reader.retry();
  assert.equal(h.reader.isReady(null),false);
  h.set(frame);
  h.reader.retry();
  assert.equal(h.current,frame);
  h.reader.destroy();
});
